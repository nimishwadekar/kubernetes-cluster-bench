from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from typing import Mapping, Sequence

from ..backends.registry import BackendRegistry, default_registry
from ..common.console import terminal_colors
from ..common.types import JsonValue
from .kubernetes import KubectlClient
from .contracts import BenchmarkConfig
from .overwrite import OverwriteDeclined, confirm_overwrite
from .orchestrator import run_benchmark
from .profile import load_profile, profile_section


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run-benchmark.sh",
        description="Run a registered Kubernetes benchmark backend.",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        required=True,
        metavar="FILE",
        help="Benchmark profile YAML file.",
    )
    return parser


def parse_config(arguments: Sequence[str] | None = None) -> BenchmarkConfig:
    args = build_parser().parse_args(arguments)
    profile = load_profile(args.profile)
    nodes = profile_section(profile, "nodes")
    dimensions = profile_section(profile, "dimensions")
    execution = profile_section(profile, "execution")

    backend = _profile_string(profile, "backend")
    if backend is None:
        raise ValueError("profile field 'backend' is required")
    backend_parameters = profile_section(execution, backend)
    parameters: dict[str, JsonValue] = {}
    for name, value in backend_parameters.items():
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise ValueError(f"profile parameter {name!r} must be a scalar")
        parameters[name] = value  # type: ignore[assignment]

    initiator_node = _profile_string(nodes, "initiator")
    target_node = _profile_string(nodes, "target")
    if initiator_node is None or target_node is None:
        raise ValueError("profile must provide nodes.initiator and nodes.target")
    if initiator_node == target_node:
        raise ValueError("initiator and target nodes must be different")

    threads_spec = _profile_thread_spec(dimensions.get("threads"))
    thread_values = _parse_thread_spec(threads_spec)
    parameters["threads"] = thread_values[0]
    parameters["threads_spec"] = threads_spec

    image = _profile_string(execution, "image")
    if image is None:
        raise ValueError("profile field 'execution.image' is required")
    namespace = _profile_string(execution, "namespace")
    kube_context, namespace = KubectlClient().context_and_namespace(namespace)
    log_directory = Path(_profile_string(execution, "log_directory") or "logs")
    name = _profile_string(profile, "name")
    test_id = _profile_string(profile, "test_id") or _default_test_id()
    runs = _profile_positive_int(execution.get("runs"), "runs", default=1)
    timeout = _profile_positive_int(
        execution.get("timeout_seconds"), "timeout_seconds", default=120
    )
    keep_resources = _profile_bool(
        execution.get("keep_resources"), "keep_resources", default=False
    )
    return BenchmarkConfig(
        backend=backend,
        test_id=test_id,
        initiator_node=initiator_node,
        target_node=target_node,
        namespace=namespace,
        image=image,
        log_directory=log_directory,
        name=name,
        kube_context=kube_context,
        runs=runs,
        timeout_seconds=timeout,
        keep_resources=keep_resources,
        parameters=parameters,
    )


def main(
    arguments: Sequence[str] | None = None,
    registry: BackendRegistry | None = None,
) -> int:
    colors = terminal_colors(sys.stdout, sys.stderr)
    try:
        selected_registry = registry or default_registry()
        config = parse_config(arguments)
        confirm_overwrite(config, colors=colors)
        execution = run_benchmark(
            config,
            selected_registry,
            colors=terminal_colors(sys.stdout, sys.stderr),
        )
    except OverwriteDeclined as error:
        print(f"{colors.yellow}{error}{colors.reset}", file=sys.stderr)
        return 1
    except (EOFError, OSError, RuntimeError, ValueError) as error:
        print(f"{colors.red}ERROR:{colors.reset} {error}", file=sys.stderr)
        return 2
    if not execution.successful:
        for outcome in execution.outcomes:
            if outcome.error is not None:
                print(
                    f"ERROR: threads={outcome.thread_count}, "
                    f"run={outcome.run_number}: {outcome.error}",
                    file=sys.stderr,
                )
        return 1
    return 0


def _profile_string(
    section: Mapping[str, object],
    name: str,
) -> str | None:
    value = section.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"profile field {name!r} must be a non-empty string")
    return value


def _profile_thread_spec(value: object) -> str:
    if value is None:
        return "1"
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("profile field 'threads' must be a string or integer")
    text = str(value)
    if not text:
        raise ValueError("profile field 'threads' must not be empty")
    return text


def _profile_positive_int(
    value: object,
    name: str,
    *,
    default: int,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"profile field {name!r} must be a positive integer")
    return value


def _profile_bool(
    value: object,
    name: str,
    *,
    default: bool,
) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"profile field {name!r} must be boolean")
    return value


def _parse_thread_spec(value: str) -> tuple[int, ...]:
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


def _default_test_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"test-{timestamp}-{os.getpid()}"
