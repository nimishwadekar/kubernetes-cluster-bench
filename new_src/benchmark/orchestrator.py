from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import time
from typing import Callable, Mapping

from ..backends.registry import BackendRegistry
from ..common.console import Colors, terminal_colors
from .artifacts import (
    BenchmarkExecution,
    RunOutcome,
    create_run_directory,
    create_test_directory,
)
from .contracts import BenchmarkConfig, RunContext
from .output import print_configuration, print_results


Report = Callable[[str], None]


def parse_thread_spec(value: str) -> tuple[int, ...]:
    """Parse an inclusive start[:end[:step]] thread range."""

    parts = value.split(":")
    if len(parts) not in (1, 2, 3) or any(not part.isdigit() for part in parts):
        raise ValueError("threads must use start[:end[:step]] with positive values")
    numbers = [int(part) for part in parts]
    start = numbers[0]
    end = start if len(numbers) == 1 else numbers[1]
    step = 1 if len(numbers) < 3 else numbers[2]
    if start < 1 or end < start or step < 1:
        raise ValueError("threads must use positive values with start <= end")
    return tuple(range(start, end + 1, step))


def run_benchmark(
    config: BenchmarkConfig,
    registry: BackendRegistry,
    *,
    report: Report | None = None,
    benchmark_started_monotonic: float | None = None,
    colors: Colors | None = None,
) -> BenchmarkExecution:
    """Run one backend through the common lifecycle and retain raw artifacts only."""

    output_colors = colors or terminal_colors()
    plugin = registry.get(config.backend)
    runner = plugin.create_runner()
    started_monotonic = (
        time.monotonic()
        if benchmark_started_monotonic is None
        else benchmark_started_monotonic
    )
    runner.validate(config)

    thread_values = _thread_values(config.parameters)
    total_runs = len(thread_values) * config.runs
    test_directory, raw_root = create_test_directory(
        config.log_directory,
        config.backend,
        config.output_name,
    )
    manifest_path = test_directory / "manifest.yaml"
    print_configuration(config, output_colors)
    runner.render_manifest(config, manifest_path)

    resources = None
    outcomes: list[RunOutcome] = []
    completed_runs = 0
    try:
        resources = runner.deploy(config, manifest_path)
        print(f"{output_colors.cyan}Network service created{output_colors.reset}")
        print(f"{output_colors.cyan}Server and client pods running{output_colors.reset}")
        for thread_count in thread_values:
            for run_number in range(1, config.runs + 1):
                parameters = dict(config.parameters)
                parameters["threads"] = thread_count
                parameters["run_number"] = run_number
                iteration_config = replace(config, parameters=parameters)
                raw_directory = create_run_directory(
                    raw_root,
                    thread_count,
                    run_number,
                )
                context = RunContext(
                    completed_runs=completed_runs,
                    total_runs=total_runs,
                    benchmark_started_monotonic=started_monotonic,
                    colors=output_colors,
                    report=report,
                )
                try:
                    returned_directory = runner.execute(
                        iteration_config,
                        resources,
                        raw_directory,
                        context,
                    )
                except Exception as error:
                    outcomes.append(
                        RunOutcome(
                            thread_count=thread_count,
                            run_number=run_number,
                            raw_directory=raw_directory,
                            error=str(error),
                        )
                    )
                    print(
                        f"{output_colors.red}benchmark test failed after "
                        f"{output_colors.reset}{error}",
                        file=sys.stderr,
                    )
                else:
                    outcome = RunOutcome(
                        thread_count=thread_count,
                        run_number=run_number,
                        raw_directory=returned_directory,
                    )
                    outcomes.append(outcome)
                    print_results(config, outcome, output_colors)
                    print(
                        f"{output_colors.green}Client benchmark completed successfully"
                        f"{output_colors.reset}\n"
                    )
                completed_runs += 1
    finally:
        runner.cleanup(config, resources)

    return BenchmarkExecution(
        backend=plugin.name,
        test_id=config.test_id,
        test_directory=test_directory,
        manifest_path=manifest_path,
        outcomes=tuple(outcomes),
    )


def _thread_values(parameters: Mapping[str, object]) -> tuple[int, ...]:
    specification = parameters.get("threads_spec")
    if isinstance(specification, str):
        return parse_thread_spec(specification)
    value = parameters.get("threads", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("threads must be a positive integer")
    return (value,)


def _report(report: Report | None, message: str) -> None:
    if report is not None:
        report(message)
