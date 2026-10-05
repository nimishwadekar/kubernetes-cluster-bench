from __future__ import annotations

import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..common.console import Colors
from .config import BenchmarkConfig, MANIFEST_TEMPLATE


class KubectlError(RuntimeError):
    """Raised when kubectl cannot complete a command."""

    def __init__(self, arguments: Sequence[str], returncode: int, stderr: str) -> None:
        command = "kubectl " + " ".join(arguments)
        detail = stderr.strip() or f"exit status {returncode}"
        super().__init__(f"{command} failed: {detail}")
        self.returncode = returncode


@dataclass(frozen=True)
class ResourceNames:
    """Kubernetes names generated for a benchmark run."""

    server_service: str
    server_pod: str
    client_pod: str

    @classmethod
    def from_run_id(cls, run_id: str) -> ResourceNames:
        server_name = f"network-benchmark-server-{run_id}"
        return cls(
            server_service=server_name,
            server_pod=server_name,
            client_pod=f"network-benchmark-client-{run_id}",
        )

    @property
    def server_host(self) -> str:
        return self.server_service

    @property
    def collision_targets(self) -> tuple[str, ...]:
        return (
            f"service/{self.server_service}",
            f"pod/{self.server_pod}",
            f"pod/{self.client_pod}",
        )


def kubectl_output(arguments: Sequence[str]) -> str:
    """Run kubectl and return stdout, preserving actionable failures."""

    result = subprocess.run(
        ["kubectl", *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise KubectlError(arguments, result.returncode, result.stderr)
    return result.stdout.rstrip("\n")


def populate_context_namespace(config_namespace: str) -> tuple[str, str]:
    """Read the current context and resolve the default namespace."""

    context = kubectl_output(["config", "current-context"])
    namespace = config_namespace
    if not namespace:
        namespace = kubectl_output(
            ["config", "view", "--minify", "--output=jsonpath={.contexts[0].context.namespace}"]
        ) or "default"
    return context, namespace


def render_manifest(config: BenchmarkConfig, rendered_manifest: Path) -> None:
    """Substitute validated benchmark values into the Kubernetes manifest."""

    manifest = MANIFEST_TEMPLATE.read_text(encoding="utf-8")
    replacements = (
        ("__RUN_ID__", config.run_id),
        ("__CLIENT_NODE__", config.client_node),
        ("__SERVER_NODE__", config.server_node),
        ("__IMAGE__", config.image),
    )
    missing = [marker for marker, _ in replacements if marker not in manifest]
    if missing:
        raise ValueError(f"manifest is missing required placeholder(s): {', '.join(missing)}")
    for marker, value in replacements:
        manifest = manifest.replace(marker, value)
    remaining = [marker for marker, _ in replacements if marker in manifest]
    if remaining:
        raise ValueError(f"manifest still contains placeholder(s): {', '.join(remaining)}")
    rendered_manifest.write_text(manifest, encoding="utf-8")


def create_rendered_manifest() -> Path:
    """Create a temporary manifest in the platform temporary directory."""

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="iperf3-benchmark.",
        suffix=".yaml",
        dir=tempfile.gettempdir(),
        delete=False,
    ) as handle:
        return Path(handle.name)


def run_checked(arguments: Sequence[str], suppress_stdout: bool = False) -> None:
    """Run kubectl with fail-fast behavior."""

    stdout = subprocess.DEVNULL if suppress_stdout else None
    subprocess.run(["kubectl", *arguments], check=True, stdout=stdout)


def resource_exists(resource: str, namespace: str) -> bool:
    """Return whether a resource exists, distinguishing errors from absence."""

    result = subprocess.run(
        ["kubectl", "get", "-n", namespace, resource, "--ignore-not-found", "-o", "name"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise KubectlError(result.args[1:], result.returncode, result.stderr)
    return bool(result.stdout.strip())


def start_server_process(
    namespace: str,
    server_pod: str,
    server_log: Path,
) -> subprocess.Popen[bytes]:
    with server_log.open("wb") as server_log_handle:
        return subprocess.Popen(
            [
                "kubectl",
                "exec",
                "-n",
                namespace,
                server_pod,
                "--",
                "iperf3",
                "-s",
            ],
            stdout=server_log_handle,
            stderr=subprocess.STDOUT,
        )


def wait_for_server_start(
    process: subprocess.Popen[bytes],
    namespace: str,
    client_pod: str,
    server_host: str,
    timeout: float,
) -> None:
    """Probe the service until iperf3 accepts connections or startup times out."""

    deadline = time.monotonic() + timeout
    probe = [
        "kubectl",
        "exec",
        "-n",
        namespace,
        client_pod,
        "--",
        "iperf3",
        "-c",
        server_host,
        "-n",
        "1K",
    ]
    while time.monotonic() < deadline:
        if process.poll() is not None:
            process.wait()
            raise RuntimeError("iperf3 server exited during startup")
        remaining = max(0.1, deadline - time.monotonic())
        try:
            result = subprocess.run(
                probe,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=min(2.0, remaining),
            )
        except subprocess.TimeoutExpired:
            continue
        if result.returncode == 0:
            return
        time.sleep(0.2)
    raise TimeoutError("timed out waiting for iperf3 server readiness")


def terminate_process(process: subprocess.Popen[bytes] | None, timeout: float = 5.0) -> None:
    """Send SIGTERM, then SIGKILL if a process does not exit promptly."""

    if process is None or process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=timeout)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def cleanup_resources(
    client_process: subprocess.Popen[bytes] | None,
    server_process: subprocess.Popen[bytes] | None,
    config: BenchmarkConfig,
    colors: Colors,
    apply_started: bool,
    rendered_manifest: Path,
) -> None:
    """Stop exec sessions, clean Kubernetes resources, and remove the manifest."""

    terminate_process(client_process)
    if server_process is not None and server_process.poll() is None:
        print(f"{colors.cyan}Stopping server benchmark session{colors.reset}")
        terminate_process(server_process)
        print(f"{colors.cyan}Server benchmark session stopped{colors.reset}")

    try:
        if not apply_started:
            return
        if config.keep_resources:
            print(f"{colors.yellow}Keeping benchmark resources (--keep-resources){colors.reset}")
            return
        print(f"{colors.cyan}Cleaning up benchmark resources{colors.reset}")
        cleanup_started = time.monotonic()
        result = subprocess.run(
            [
                "kubectl",
                "delete",
                "-n",
                config.namespace,
                "-f",
                str(rendered_manifest),
                "--ignore-not-found",
            ],
            check=False,
            text=True,
        )
        if result.returncode == 0:
            cleanup_duration = int(time.monotonic() - cleanup_started)
            print(f"{colors.green}Benchmark resources deleted ({cleanup_duration}s){colors.reset}")
        else:
            print(
                f"{colors.yellow}warning:{colors.reset} failed to clean up Kubernetes "
                f"resources for run {config.run_id}",
                file=sys.stderr,
            )
    finally:
        rendered_manifest.unlink(missing_ok=True)
