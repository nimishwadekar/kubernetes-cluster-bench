from __future__ import annotations

from pathlib import Path

from ..common.console import Colors
from .artifacts import RunOutcome
from .contracts import BenchmarkConfig


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def print_configuration(config: BenchmarkConfig, colors: Colors) -> None:
    duration_set = config.parameters.get("transfer_size") is None
    duration = config.parameters.get("duration_seconds", 10)
    duration_display = f"{duration} seconds" if duration_set else "N/A"
    transfer_display = (
        "N/A" if duration_set else str(config.parameters.get("transfer_size", ""))
    )
    print(f"\n{colors.cyan}Benchmark configuration{colors.reset}")
    print(f"  {'Test ID:':<20} {config.test_id}")
    print(f"  {'Kubernetes context:':<20} {config.kube_context}")
    print(f"  {'Client node:':<20} {config.initiator_node}")
    print(f"  {'Server node:':<20} {config.target_node}")
    print(f"  {'Namespace:':<20} {config.namespace}")
    print(f"  {'Logs directory:':<20} {config.log_directory}")
    print(f"  {'Image:':<20} {config.image}")
    print(f"  {'Protocol:':<20} TCP")
    print(f"  {'Duration:':<20} {duration_display}")
    if duration_set:
        print(f"  {'Warmup:':<20} {config.parameters.get('warmup_seconds', 5)} seconds")
    print(f"  {'Transfer size:':<20} {transfer_display}")
    print(f"  {'Threads:':<20} {config.parameters.get('threads_spec', '1')}")
    print(f"  {'Runs per test:':<20} {config.runs}\n")


def print_results(
    config: BenchmarkConfig,
    outcome: RunOutcome,
    colors: Colors,
) -> None:
    run_directory = outcome.raw_directory
    server_path = run_directory.parent / f"{config.test_id}-server.json"
    print(f"\n{colors.cyan}Results{colors.reset}")
    print(
        f"  {colors.cyan}Client result:{colors.reset} "
        f"{colors.green}{display_path(_client_path(config, run_directory, outcome.run_number))}{colors.reset}"
    )
    print(
        f"  {colors.cyan}Server log:{colors.reset}    "
        f"{colors.green}{display_path(server_path)}{colors.reset}"
    )
    print(
        f"  {colors.cyan}Raw artifacts:{colors.reset} "
        f"{colors.green}{display_path(run_directory)}{colors.reset}"
    )


def _client_path(config: BenchmarkConfig, run_directory: Path, run_number: int) -> Path:
    thread = run_directory.name.removeprefix("t")
    return run_directory / f"{config.test_id}-t{thread}-run{run_number}-client.json"
