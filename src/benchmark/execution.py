from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from ..common.console import Colors
from .config import BenchmarkConfig, BenchmarkExit, PROJECT_ROOT, PROGRESS_WIDTH, fail
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


@dataclass
class BenchmarkIteration:
    config: BenchmarkConfig
    paths: RunPaths
    metadata: Metadata
    run_number: int
    finalized: bool = False


MAX_CLIENT_ATTEMPTS = 3


def _run_paths(config: BenchmarkConfig) -> RunPaths:
    log_dir = Path(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    return RunPaths(
        log_dir=log_dir,
        client_log=log_dir / f"{config.test_id}-client.json",
        server_log=log_dir / f"{config.test_id}-server.json",
        metadata_log=log_dir / f"{config.test_id}-metadata.json",
        rendered_manifest=create_rendered_manifest(),
    )


def _iteration_config(config: BenchmarkConfig, thread_count: int) -> BenchmarkConfig:
    return replace(
        config,
        threads=thread_count,
        threads_text=(
            config.threads_text
            if config.thread_spec.is_single
            else str(thread_count)
        ),
        test_id=config.test_id,
    )


def _iteration_paths(
    config: BenchmarkConfig, shared_paths: RunPaths, run_number: int
) -> RunPaths:
    stem = f"{config.test_id}-t{config.threads}-run{run_number}"
    return RunPaths(
        log_dir=shared_paths.log_dir,
        client_log=shared_paths.log_dir / f"{stem}-client.json",
        server_log=shared_paths.server_log,
        metadata_log=shared_paths.log_dir / f"{stem}-metadata.json",
        rendered_manifest=shared_paths.rendered_manifest,
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
    print(f"  {'Test ID:':<20} {config.test_id}")
    print(f"  {'Kubernetes context:':<20} {kube_context}")
    print(f"  {'Client node:':<20} {config.client_node}")
    print(f"  {'Server node:':<20} {config.server_node}")
    print(f"  {'Namespace:':<20} {config.namespace}")
    print(f"  {'Logs directory:':<20} {config.log_dir}")
    print(f"  {'Image:':<20} {config.image}")
    print(f"  {'Protocol:':<20} TCP")
    print(f"  {'Duration:':<20} {duration_display}")
    if config.duration_set:
        print(f"  {'Warmup:':<20} {config.warmup_text} seconds")
    print(f"  {'Transfer size:':<20} {transfer_size_display}")
    print(f"  {'Threads:':<20} {config.threads_text}")
    print(f"  {'Runs per test:':<20} {config.run_text}\n")


def _wait_for_client(
    client_process: subprocess.Popen[bytes], config: BenchmarkConfig, colors: Colors
) -> int:
    if not sys.stdout.isatty() or not config.duration_set:
        return client_process.wait()

    started = time.monotonic()
    while client_process.poll() is None:
        elapsed = time.monotonic() - started
        total_duration = config.duration + config.warmup
        progress = min(int(elapsed * 100 / total_duration), 99)
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
            fail(
                f"resource already exists: {resource}; retry with a clean namespace",
                colors,
            )


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
        str(config.threads),
        "--get-server-output",
    ]
    if config.transfer_size is not None:
        command.extend(["-n", config.transfer_size])
    else:
        command.extend(
            ["-t", str(config.duration + config.warmup), "-O", str(config.warmup)]
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
    display_results: bool = True,
) -> None:
    metadata["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    metadata["client_status"] = client_status
    write_json_metadata(metadata_log, metadata)
    if display_results:
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
    """Deploy one pod pair and execute all requested thread-count benchmarks."""

    duration_display = f"{config.duration_text} seconds" if config.duration_set else "N/A"
    transfer_size_display = "N/A" if config.duration_set else config.transfer_size or ""
    paths = _run_paths(config)
    resources = ResourceNames.from_test_id(config.test_id)
    iterations: list[BenchmarkIteration] = []
    apply_started = False
    server_process: subprocess.Popen[bytes] | None = None
    client_process: subprocess.Popen[bytes] | None = None
    failed_tests: list[str] = []
    run_status = 1

    try:
        render_manifest(config, paths.rendered_manifest)
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for thread_count in config.thread_spec.values:
            iteration_config = _iteration_config(config, thread_count)
            for run_number in range(1, config.runs + 1):
                iteration_paths = _iteration_paths(iteration_config, paths, run_number)
                iteration_metadata = initial_metadata(
                    iteration_config,
                    resources,
                    kube_context,
                    duration_display,
                    transfer_size_display,
                    started_at,
                    run_number,
                )
                write_json_metadata(iteration_paths.metadata_log, iteration_metadata)
                iterations.append(
                    BenchmarkIteration(
                        config=iteration_config,
                        paths=iteration_paths,
                        metadata=iteration_metadata,
                        run_number=run_number,
                    )
                )

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
        for iteration in iterations:
            client_status = 1
            for attempt in range(1, MAX_CLIENT_ATTEMPTS + 1):
                iteration.paths.client_log.write_bytes(b"")
                attempt_text = (
                    ""
                    if attempt == 1
                    else f" (attempt {attempt}/{MAX_CLIENT_ATTEMPTS})"
                )
                print(
                    f"{colors.cyan}Client benchmark running with "
                    f"{iteration.config.threads} thread(s) "
                    f"(run {iteration.run_number}/{iteration.config.runs})"
                    f"{attempt_text}{colors.reset}"
                )
                with iteration.paths.client_log.open("wb") as client_log_handle:
                    client_process = subprocess.Popen(
                        _client_command(iteration.config, resources),
                        stdout=client_log_handle,
                        stderr=subprocess.STDOUT,
                    )

                client_status_raw = _wait_for_client(
                    client_process, iteration.config, colors
                )
                client_status = (
                    client_status_raw
                    if client_status_raw >= 0
                    else 128 + (-client_status_raw)
                )
                client_process = None
                if client_status == 0:
                    break
                if attempt < MAX_CLIENT_ATTEMPTS:
                    print(
                        f"{colors.yellow}benchmark attempt failed with status "
                        f"{client_status}; retrying{colors.reset}",
                        file=sys.stderr,
                    )

            if client_status != 0:
                failed_tests.append(
                    f"{iteration.paths.metadata_log.stem} (client_status={client_status})"
                )
            _write_results(
                iteration.metadata,
                iteration.paths.metadata_log,
                client_status,
                iteration.paths.client_log,
                iteration.paths.server_log,
                colors,
            )
            iteration.finalized = True

            if client_status != 0:
                print(
                    f"{colors.red}benchmark test failed after "
                    f"{MAX_CLIENT_ATTEMPTS} attempts:{colors.reset} client output was "
                    f"saved to {iteration.paths.client_log}; continuing",
                    file=sys.stderr,
                )
                continue
            print(f"{colors.green}Client benchmark completed successfully{colors.reset}\n")

        if failed_tests:
            print(
                f"{colors.red}benchmark completed with failed test(s):{colors.reset} "
                + ", ".join(failed_tests),
                file=sys.stderr,
            )
            run_status = 1
            return 1
        run_status = 0
        return 0
    except BenchmarkExit as error:
        run_status = error.code
        raise
    finally:
        for iteration in iterations:
            if not iteration.finalized:
                _write_results(
                    iteration.metadata,
                    iteration.paths.metadata_log,
                    run_status,
                    iteration.paths.client_log,
                    iteration.paths.server_log,
                    colors,
                    display_results=False,
                )
        cleanup_resources(
            client_process,
            server_process,
            config,
            colors,
            apply_started,
            paths.rendered_manifest,
        )
