from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from ..backends.registry import BackendRegistry, default_registry
from ..common.console import terminal_colors
from ..common.types import JsonValue
from .kubernetes import KubectlClient
from .contracts import BenchmarkConfig
from .overwrite import OverwriteDeclined, confirm_overwrite
from .orchestrator import run_benchmark


DEFAULT_IMAGE = "quay.io/nwadekar/kubernetes-cluster-bench:latest"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run-benchmark.sh",
        description="Run a registered Kubernetes benchmark backend.",
    )
    parser.add_argument("--backend", required=True, metavar="BACKEND")
    parser.add_argument(
        "-c",
        "--client",
        "--initiator",
        dest="initiator_node",
        required=True,
        metavar="NODE",
    )
    parser.add_argument(
        "-s",
        "--server",
        "--target",
        dest="target_node",
        required=True,
        metavar="NODE",
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE, metavar="IMAGE")
    parser.add_argument("-d", "--dir", dest="log_directory", type=Path, default=Path("logs"))
    parser.add_argument("-n", "--namespace", default=None, metavar="NAMESPACE")
    parser.add_argument("--benchmark", default="benchmark", metavar="NAME")
    parser.add_argument("--test-id", default=None, metavar="ID")
    parser.add_argument(
        "--name",
        default=None,
        metavar="NAME",
        help="Log directory name (default: test ID).",
    )
    parser.add_argument("--duration", type=positive_int, default=None, metavar="SECONDS")
    parser.add_argument("--transfer-size", default=None, metavar="SIZE")
    parser.add_argument("--warmup", type=nonnegative_int, default=None, metavar="SECONDS")
    parser.add_argument("--threads", default="1", metavar="SPEC")
    parser.add_argument("-r", "--run", dest="runs", type=positive_int, default=1)
    parser.add_argument("--timeout", type=positive_int, default=120, metavar="SECONDS")
    parser.add_argument(
        "--parameter",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Backend parameter; may be repeated.",
    )
    parser.add_argument("--keep-resources", action="store_true")
    return parser


def parse_config(arguments: Sequence[str] | None = None) -> BenchmarkConfig:
    args = build_parser().parse_args(arguments)
    if args.initiator_node == args.target_node:
        raise ValueError("initiator and target nodes must be different")
    thread_values = _parse_thread_spec(args.threads)
    if args.duration is not None and args.transfer_size is not None:
        raise ValueError("--duration and --transfer-size cannot be used together")
    if args.warmup is not None and args.transfer_size is not None:
        raise ValueError("--warmup requires --duration")
    parameters: dict[str, JsonValue] = {
        "threads": thread_values[0],
        "threads_spec": args.threads,
    }
    if args.duration is not None:
        parameters["duration_seconds"] = args.duration
    if args.transfer_size is not None:
        parameters["transfer_size"] = args.transfer_size
    if args.warmup is not None:
        parameters["warmup_seconds"] = args.warmup
    parameters.update(_parse_parameters(args.parameter))
    kube_context, namespace = KubectlClient().context_and_namespace(args.namespace)
    return BenchmarkConfig(
        backend=args.backend,
        benchmark=args.benchmark,
        test_id=args.test_id or _default_test_id(),
        initiator_node=args.initiator_node,
        target_node=args.target_node,
        namespace=namespace,
        image=args.image,
        log_directory=args.log_directory,
        name=args.name,
        kube_context=kube_context,
        runs=args.runs,
        timeout_seconds=args.timeout,
        keep_resources=args.keep_resources,
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


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


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


def _parse_parameters(values: Sequence[str]) -> dict[str, JsonValue]:
    parameters: dict[str, JsonValue] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--parameter must use NAME=VALUE")
        name, raw_value = value.split("=", 1)
        if not name:
            raise ValueError("--parameter name must not be empty")
        try:
            parsed: object = json.loads(raw_value)
        except json.JSONDecodeError:
            parsed = raw_value
        if not isinstance(parsed, (str, int, float, bool)) and parsed is not None:
            raise ValueError(f"--parameter {name!r} must be a scalar JSON value")
        parameters[name] = parsed  # type: ignore[assignment]
    return parameters


def _default_test_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"test-{timestamp}-{os.getpid()}"
