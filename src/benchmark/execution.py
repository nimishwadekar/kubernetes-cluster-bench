from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..common.console import Colors
from .config import BenchmarkConfig, PROJECT_ROOT, PROGRESS_WIDTH, fail
from .kubernetes import (
    ResourceNames,
    cleanup_resources,
    create_rendered_manifest,
    render_manifest,
    resource_exists,
    run_checked,
    start_server_process,
    terminate_process,
    wait_for_server_start,
)
from .metadata import Metadata, initial_metadata, write_json_metadata


@dataclass(frozen=True)
class RunPaths:
    log_dir: Path
    client_log: Path
    server_log: Path
    metadata_log: Path
    rendered_manifest: Path


def _run_paths(config: BenchmarkConfig) -> RunPaths:
    log_dir = Path(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    return RunPaths(
        log_dir=log_dir,
        client_log=log_dir / f"{config.run_id}-client.json",
        server_log=log_dir / f"{config.run_id}-server.log",
        metadata_log=log_dir / f"{config.run_id}-metadata.json",
        rendered_manifest=create_rendered_manifest(),
    )


def display_path(path: Path) -> str:
    """Shorten paths under the repository root for command output."""

    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def print_progress(percent: int, colors: Colors) -> None:
    """Print the command's progress bar."""

    filled = percent * PROGRESS_WIDTH // 100
    empty = PROGRESS_WIDTH - filled
    print(
        f"\r{colors.cyan}Client benchmark progress: [{'#' * filled}{'.' * empty}] "
        f"{percent:3d}%{colors.reset}",
        end="",
        flush=True,
    )


def print_configuration(
    config: BenchmarkConfig,
    kube_context: str,
    duration_display: str,
    transfer_size_display: str,
    colors: Colors,
) -> None:
    """Print the benchmark configuration block."""

    print(f"\n{colors.cyan}Benchmark configuration{colors.reset}")
    print(f"  {'Run ID:':<20} {config.run_id}")
    print(f"  {'Kubernetes context:':<20} {kube_context}")
    print(f"  {'Client node:':<20} {config.client_node}")
    print(f"  {'Server node:':<20} {config.server_node}")
    print(f"  {'Namespace:':<20} {config.namespace}")
    print(f"  {'Logs directory:':<20} {config.log_dir}")
    print(f"  {'Image:':<20} {config.image}")
    print(f"  {'Protocol:':<20} TCP")
    print(f"  {'Duration:':<20} {duration_display}")
    print(f"  {'Transfer size:':<20} {transfer_size_display}")
    print(f"  {'Parallel streams:':<20} {config.parallel_streams_text}\n")


def _wait_for_client(
    client_process: subprocess.Popen[bytes], config: BenchmarkConfig, colors: Colors
) -> int:
    if not sys.stdout.isatty() or not config.duration_set:
        return client_process.wait()

    started = time.monotonic()
    while client_process.poll() is None:
        elapsed = time.monotonic() - started
        progress = min(int(elapsed * 100 / config.duration), 99)
        print_progress(progress, colors)
        time.sleep(1)

    status = client_process.wait()
    if status == 0:
        print_progress(100, colors)
    print()
    return status


def _check_for_collisions(
    resources: ResourceNames, config: BenchmarkConfig, colors: Colors
) -> None:
    for resource in resources.collision_targets:
        if resource_exists(resource, config.namespace):
            fail(f"resource already exists: {resource}; choose a different --run-id", colors)


def _wait_for_pods(config: BenchmarkConfig, resources: ResourceNames) -> None:
    for pod in (resources.server_pod, resources.client_pod):
        run_checked(
            [
                "wait",
                "-n",
                config.namespace,
                "--for=jsonpath={.status.phase}=Running",
                f"pod/{pod}",
                f"--timeout={config.timeout_text}s",
            ],
            suppress_stdout=True,
        )


def _client_command(config: BenchmarkConfig, resources: ResourceNames) -> list[str]:
    command = [
        "kubectl",
        "exec",
        "-n",
        config.namespace,
        resources.client_pod,
        "--",
        "iperf3",
        "-c",
        resources.server_host,
        "-J",
        "-P",
        str(config.parallel_streams),
        "--get-server-output",
    ]
    command.extend(
        ["-n", config.transfer_size]
        if config.transfer_size is not None
        else ["-t", str(config.duration)]
    )
    return command


def _start_server(
    config: BenchmarkConfig,
    resources: ResourceNames,
    server_log: Path,
    colors: Colors,
) -> subprocess.Popen[bytes]:
    server_process = start_server_process(config.namespace, resources.server_pod, server_log)
    try:
        wait_for_server_start(
            server_process,
            config.namespace,
            resources.server_pod,
            timeout=float(config.timeout),
        )
    except (RuntimeError, TimeoutError):
        fail(f"iperf3 server failed to become ready; see {server_log}", colors)
    print(f"{colors.cyan}Server benchmark running{colors.reset}")
    return server_process


def _write_results(
    metadata: Metadata,
    metadata_log: Path,
    client_status: int,
    client_log: Path,
    server_log: Path,
    colors: Colors,
) -> None:
    metadata["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    metadata["client_status"] = client_status
    write_json_metadata(metadata_log, metadata)
    print(f"\n{colors.cyan}Results{colors.reset}")
    print(
        f"  {colors.cyan}Client result:{colors.reset} "
        f"{colors.green}{display_path(client_log)}{colors.reset}"
    )
    print(
        f"  {colors.cyan}Server log:{colors.reset}    "
        f"{colors.green}{display_path(server_log)}{colors.reset}"
    )
    print(
        f"  {colors.cyan}Metadata:{colors.reset}      "
        f"{colors.green}{display_path(metadata_log)}{colors.reset}"
    )


def run_benchmark(config: BenchmarkConfig, kube_context: str, colors: Colors) -> int:
    """Deploy, execute, collect, and clean up one benchmark run."""

    duration_display = f"{config.duration_text} seconds" if config.duration_set else "N/A"
    transfer_size_display = "N/A" if config.duration_set else config.transfer_size or ""
    paths = _run_paths(config)
    resources = ResourceNames.from_run_id(config.run_id)
    apply_started = False
    server_process: subprocess.Popen[bytes] | None = None
    client_process: subprocess.Popen[bytes] | None = None

    try:
        render_manifest(config, paths.rendered_manifest)
        metadata = initial_metadata(
            config,
            resources,
            kube_context,
            duration_display,
            transfer_size_display,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        write_json_metadata(paths.metadata_log, metadata)
        print_configuration(config, kube_context, duration_display, transfer_size_display, colors)

        _check_for_collisions(resources, config, colors)
        apply_started = True
        run_checked(
            ["apply", "-n", config.namespace, "-f", str(paths.rendered_manifest)],
            suppress_stdout=True,
        )
        print(f"{colors.cyan}Network service created{colors.reset}")
        _wait_for_pods(config, resources)
        print(f"{colors.cyan}Server and client pods running{colors.reset}")

        server_process = _start_server(config, resources, paths.server_log, colors)
        client_command = _client_command(config, resources)
        print(f"{colors.cyan}Client benchmark running{colors.reset}")
        with paths.client_log.open("wb") as client_log_handle:
            client_process = subprocess.Popen(
                client_command,
                stdout=client_log_handle,
                stderr=subprocess.STDOUT,
            )

        client_status_raw = _wait_for_client(client_process, config, colors)
        client_status = client_status_raw if client_status_raw >= 0 else 128 + (-client_status_raw)
        client_process = None
        terminate_process(server_process)
        server_process = None
        _write_results(
            metadata,
            paths.metadata_log,
            client_status,
            paths.client_log,
            paths.server_log,
            colors,
        )

        if client_status != 0:
            print(
                f"{colors.red}benchmark failed:{colors.reset} client output was saved to "
                f"{paths.client_log}",
                file=sys.stderr,
            )
            return 1
        print(f"{colors.green}Client benchmark completed successfully{colors.reset}\n")
        return 0
    finally:
        cleanup_resources(
            client_process,
            server_process,
            config,
            colors,
            apply_started,
            paths.rendered_manifest,
        )
