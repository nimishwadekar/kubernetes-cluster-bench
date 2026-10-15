from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import ipaddress
import json
import re

import yaml

from .kubernetes import KubectlClient, KubectlError
from .manifest import dump_manifest


NETWORKS_ANNOTATION = "k8s.v1.cni.cncf.io/networks"
NETWORK_STATUS_ANNOTATION = "k8s.v1.cni.cncf.io/network-status"
DEFAULT_INTERFACE = "net1"
NETWORK_ATTACHMENT_RESOURCE = "network-attachment-definitions.k8s.cni.cncf.io"
_NAME = r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?"
_ATTACHMENT_PATTERN = re.compile(rf"(?:{_NAME}/)?{_NAME}")
_INTERFACE_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,15}")


@dataclass(frozen=True)
class NetworkAttachment:
    """One pod's Multus secondary interface."""

    attachment: str
    interface: str = DEFAULT_INTERFACE
    ip: str | None = None

    def __post_init__(self) -> None:
        if _ATTACHMENT_PATTERN.fullmatch(self.attachment) is None:
            raise ValueError(
                "attachment must be a NetworkAttachmentDefinition name "
                "or <namespace>/<name>"
            )
        if _INTERFACE_PATTERN.fullmatch(self.interface) is None:
            raise ValueError(
                "interface must be 1-15 characters of letters, numbers, "
                "dots, underscores, and hyphens"
            )
        if self.ip is not None:
            _parse_cidr(self.ip, "ip")


@dataclass(frozen=True)
class SecondaryNetwork:
    """Secondary interfaces for the initiator and target pods.

    The two sides are configured independently because they usually sit on
    different nodes with different NICs, and so different attachments.
    """

    initiator: NetworkAttachment
    target: NetworkAttachment

    def for_role(self, role: str) -> NetworkAttachment:
        return self.initiator if role == "initiator" else self.target


def _parse_cidr(value: str, name: str) -> ipaddress.IPv4Interface | ipaddress.IPv6Interface:
    if "/" not in value:
        raise ValueError(f"{name} must include a prefix length, e.g. 10.10.0.2/24")
    try:
        return ipaddress.ip_interface(value)
    except ValueError as error:
        raise ValueError(f"{name} is not a valid address: {value!r}") from error


def networks_annotation(attachment: NetworkAttachment) -> str:
    """Return the Multus ``networks`` annotation selecting ``attachment``."""

    namespace, _, name = attachment.attachment.rpartition("/")
    selection: dict[str, object] = {"name": name}
    if namespace:
        selection["namespace"] = namespace
    selection["interface"] = attachment.interface
    if attachment.ip is not None:
        selection["ips"] = [attachment.ip]
    return json.dumps([selection])


def apply_secondary_network(
    manifest: str,
    network: SecondaryNetwork,
    pod_roles: Mapping[str, str],
) -> str:
    """Annotate each named pod in ``manifest`` with its Multus selection."""

    documents = [doc for doc in yaml.safe_load_all(manifest) if doc is not None]
    annotated: set[str] = set()
    for document in documents:
        if document.get("kind") != "Pod":
            continue
        metadata = document.setdefault("metadata", {})
        role = pod_roles.get(metadata.get("name", ""))
        if role is None:
            continue
        annotations = metadata.setdefault("annotations", {})
        annotations[NETWORKS_ANNOTATION] = networks_annotation(network.for_role(role))
        annotated.add(metadata["name"])
    missing = sorted(set(pod_roles) - annotated)
    if missing:
        raise ValueError(
            "manifest has no Pod named " + ", ".join(missing)
            + " to attach the secondary network to"
        )
    return dump_manifest(documents)


def discover_interface_address(
    kubectl: KubectlClient,
    namespace: str,
    pod: str,
    interface: str,
) -> str:
    """Return the first IP Multus reports for ``interface`` inside ``pod``."""

    raw = kubectl.output(
        [
            "get",
            "pod",
            pod,
            "-n",
            namespace,
            "-o",
            "jsonpath={.metadata.annotations."
            + NETWORK_STATUS_ANNOTATION.replace(".", "\\.")
            + "}",
        ]
    )
    if not raw:
        raise ValueError(
            f"pod {pod} has no {NETWORK_STATUS_ANNOTATION} annotation; "
            "is Multus installed and the attachment valid?"
        )
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"pod {pod} has an unreadable network-status: {error}") from error
    if not isinstance(entries, list):
        raise ValueError(f"pod {pod} has an unexpected network-status format")
    for entry in entries:
        if isinstance(entry, dict) and entry.get("interface") == interface:
            ips = entry.get("ips") or []
            if not ips:
                raise ValueError(
                    f"interface {interface} of pod {pod} has no IP address; "
                    "check the attachment's IPAM configuration"
                )
            return str(ips[0])
    available = ", ".join(
        str(entry.get("interface")) for entry in entries if isinstance(entry, dict)
    )
    raise ValueError(
        f"pod {pod} has no interface {interface!r} (found: {available or 'none'})"
    )


def set_container_env(
    manifest: str,
    pod_env: Mapping[str, Mapping[str, str]],
) -> str:
    """Set environment variables on every container of the named pods."""

    documents = [doc for doc in yaml.safe_load_all(manifest) if doc is not None]
    found: set[str] = set()
    for document in documents:
        if document.get("kind") != "Pod":
            continue
        name = document.get("metadata", {}).get("name", "")
        if name not in pod_env:
            continue
        found.add(name)
        for container in document.get("spec", {}).get("containers", []):
            env = container.setdefault("env", [])
            for variable, value in pod_env[name].items():
                for entry in env:
                    if entry.get("name") == variable:
                        entry["value"] = value
                        break
                else:
                    env.append({"name": variable, "value": value})
    missing = sorted(set(pod_env) - found)
    if missing:
        raise ValueError("manifest has no Pod named " + ", ".join(missing))
    return dump_manifest(documents)


def discover_addresses(
    kubectl: KubectlClient,
    namespace: str,
    network: SecondaryNetwork,
    pods: Mapping[str, str],
) -> dict[str, str]:
    """Return ``{"initiator_address": ..., "target_address": ...}`` for the pods.

    ``pods`` maps each role to its pod name. A requested static IP that the
    attachment did not honor fails here, before any traffic is generated.
    """

    addresses: dict[str, str] = {}
    for role in ("initiator", "target"):
        attachment = network.for_role(role)
        address = discover_interface_address(
            kubectl, namespace, pods[role], attachment.interface
        )
        check_requested_address(attachment, pods[role], address)
        addresses[f"{role}_address"] = address
    return addresses


def verify_attachments(
    kubectl: KubectlClient,
    namespace: str,
    network: SecondaryNetwork,
) -> None:
    """Fail early if Multus is missing or an attachment definition does not exist.

    Without Multus the annotation is silently ignored and the pods start with
    only ``eth0``, so this turns that into an immediate, explicit error.
    """

    checked: set[tuple[str, str]] = set()
    for side, selection in (("initiator", network.initiator), ("target", network.target)):
        attachment_namespace, _, name = selection.attachment.rpartition("/")
        key = (attachment_namespace or namespace, name)
        if key in checked:
            continue
        checked.add(key)
        try:
            found = kubectl.resource_exists(
                f"{NETWORK_ATTACHMENT_RESOURCE}/{name}", key[0]
            )
        except KubectlError as error:
            message = error.stderr.lower()
            if (
                "doesn't have a resource type" in message
                or "no matches for kind" in message
            ):
                raise ValueError(
                    "network section requires Multus, but the cluster has no "
                    f"NetworkAttachmentDefinition resource ({NETWORK_ATTACHMENT_RESOURCE}); "
                    "install Multus or remove the network section"
                ) from error
            # Other failures (e.g. RBAC) must not block a valid setup; address
            # discovery after deployment still reports a missing interface.
            continue
        if not found:
            raise ValueError(
                f"NetworkAttachmentDefinition {name!r} (network.{side}) not found "
                f"in namespace {key[0]!r}; create it first or fix network.{side}.attachment"
            )


def check_requested_address(
    attachment: NetworkAttachment, pod: str, discovered: str
) -> None:
    """Fail if the attachment did not honor a requested static IP."""

    if attachment.ip is None:
        return
    expected = _parse_cidr(attachment.ip, "ip").ip
    if ipaddress.ip_address(discovered) != expected:
        raise ValueError(
            f"pod {pod} got {discovered} on {attachment.interface} instead of the "
            f"requested {expected}; the attachment's IPAM may not accept requested IPs"
        )
