from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re

import yaml

from .manifest import dump_manifest


RESOURCE_NAMES = ("cpu", "memory")
_CPU = re.compile(r"(\d+(?:\.\d+)?)(m?)")
_MEMORY = re.compile(r"(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E)?")
_MEMORY_FACTORS = {
    "": 1,
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5, "Ei": 1024**6,
    "k": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4, "P": 1000**5, "E": 1000**6,
}


def _empty() -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class PodResources:
    """CPU and memory settings applied to every benchmark container.

    Only the values given here replace the manifest's own; anything left out
    keeps the manifest default.
    """

    requests: Mapping[str, str] = field(default_factory=_empty)
    limits: Mapping[str, str] = field(default_factory=_empty)

    def __post_init__(self) -> None:
        for section, values in (("requests", self.requests), ("limits", self.limits)):
            for name, value in values.items():
                if name not in RESOURCE_NAMES:
                    raise ValueError(
                        f"resources.{section}.{name} is not supported "
                        f"(use {', '.join(RESOURCE_NAMES)})"
                    )
                try:
                    quantity(name, value)
                except ValueError as error:
                    raise ValueError(f"resources.{section}.{name}: {error}") from error

    @property
    def is_empty(self) -> bool:
        return not self.requests and not self.limits


def quantity(name: str, value: str) -> float:
    """Return a CPU quantity in cores or a memory quantity in bytes."""

    text = str(value)
    if name == "cpu":
        match = _CPU.fullmatch(text)
        if match is None:
            raise ValueError(f"{text!r} is not a CPU quantity (e.g. 2, 0.5 or 500m)")
        return float(match.group(1)) / (1000 if match.group(2) else 1)
    match = _MEMORY.fullmatch(text)
    if match is None:
        raise ValueError(f"{text!r} is not a memory quantity (e.g. 4Gi or 512Mi)")
    return float(match.group(1)) * _MEMORY_FACTORS[match.group(2) or ""]


def apply_resources(manifest: str, resources: PodResources) -> str:
    """Override cpu/memory requests and limits on every container of every Pod."""

    documents = [doc for doc in yaml.safe_load_all(manifest) if doc is not None]
    for document in documents:
        if document.get("kind") != "Pod":
            continue
        pod = document.get("metadata", {}).get("name", "")
        for container in document.get("spec", {}).get("containers", []):
            settings = container.setdefault("resources", {})
            for section, values in (
                ("requests", resources.requests),
                ("limits", resources.limits),
            ):
                settings.setdefault(section, {}).update(values)
            for name in RESOURCE_NAMES:
                request = settings["requests"].get(name)
                limit = settings["limits"].get(name)
                if (
                    request is not None
                    and limit is not None
                    and quantity(name, request) > quantity(name, limit)
                ):
                    raise ValueError(
                        f"resources: {name} request {request} exceeds limit {limit} "
                        f"for pod {pod}; adjust resources.requests.{name} or "
                        f"resources.limits.{name}"
                    )
    return dump_manifest(documents)
