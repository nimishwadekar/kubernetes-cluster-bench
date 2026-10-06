from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn, Sequence

from ..common.console import Colors


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_TEMPLATE = PROJECT_ROOT / "network-benchmark.yaml"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_IMAGE = "quay.io/nwadekar/kubernetes-cluster-bench:latest"
DEFAULT_DURATION = "10"
DEFAULT_WARMUP = "5"
DEFAULT_THREADS = "1"
DEFAULT_TIMEOUT = "120"
RUN_ID_LIMIT = 38
PROGRESS_WIDTH = 30

@dataclass(frozen=True)
class ParsedConfig:
    """Raw command-line values retained until validation."""

    client_node: str
    server_node: str
    image: str
    namespace: str
    duration_text: str
    duration_set: bool
    warmup_text: str
    warmup_set: bool
    transfer_size: str | None
    threads_text: str
    timeout_text: str
    keep_resources: bool
    run_id: str
    log_dir: str


@dataclass(frozen=True)
class ThreadSpec:
    """Inclusive thread-count values requested by the user."""

    start: int
    end: int
    step: int

    @property
    def values(self) -> range:
        return range(self.start, self.end + 1, self.step)

    @property
    def is_single(self) -> bool:
        return self.start + self.step > self.end


@dataclass(frozen=True)
class BenchmarkConfig:
    """Validated settings used by one benchmark run."""

    client_node: str
    server_node: str
    image: str
    namespace: str
    duration: int
    duration_text: str
    duration_set: bool
    warmup: int
    warmup_text: str
    transfer_size: str | None
    threads: int
    threads_text: str
    thread_spec: ThreadSpec
    timeout: int
    timeout_text: str
    keep_resources: bool
    run_id: str
    log_dir: str


class BenchmarkExit(Exception):
    """Expected command exit carrying the shell runner's status code."""

    def __init__(self, code: int) -> None:
        super().__init__()
        self.code = code


class BenchmarkArgumentParser(argparse.ArgumentParser):
    """Argparse parser that includes full help text for parse errors."""

    def error(self, message: str) -> NoReturn:
        self._print_message(f"{self.prog}: error: {message}\n\n", sys.stderr)
        self.print_help(sys.stderr)
        self.exit(2)


def fail(message: str, colors: Colors) -> NoReturn:
    """Print a non-argument error and return shell status two."""

    print(f"{colors.red}ERROR:{colors.reset} {message}", file=sys.stderr)
    raise BenchmarkExit(2)


def argument_error(message: str, colors: Colors) -> NoReturn:
    """Report a validated argument error after argparse has parsed the CLI."""

    print(f"{colors.red}ERROR:{colors.reset} {message}", file=sys.stderr)
    raise BenchmarkExit(2)


def utc_timestamp(format_string: str) -> str:
    return datetime.now(timezone.utc).strftime(format_string)


def make_parser() -> argparse.ArgumentParser:
    parser = BenchmarkArgumentParser(
        prog="./run-benchmark.sh",
        usage="%(prog)s --client NODE --server NODE [options]",
        description="Run an iperf3 network benchmark between two Kubernetes nodes.",
        formatter_class=argparse.HelpFormatter,
        add_help=False,
        allow_abbrev=False,
    )
    required = parser.add_argument_group("Required")
    options = parser.add_argument_group("Options")
    required.add_argument(
        "-c", "--client", required=True, metavar="NODE",
        help="Kubernetes node for the client pod",
    )
    required.add_argument(
        "-s", "--server", required=True, metavar="NODE",
        help="Kubernetes node for the server pod",
    )
    options.add_argument(
        "--image", default=DEFAULT_IMAGE, metavar="IMAGE",
        help=f"Benchmark image (default: {DEFAULT_IMAGE})",
    )
    options.add_argument(
        "-d", "--dir", dest="log_dir", default=str(DEFAULT_LOG_DIR), metavar="DIR",
        help="Directory for benchmark logs (default: <PWD>/logs)",
    )
    options.add_argument(
        "-n", "--namespace", default="", metavar="NAMESPACE",
        help="Kubernetes namespace (default: current context namespace)",
    )
    options.add_argument(
        "--duration", default=None, metavar="SECONDS",
        help="Test duration (default: 10; cannot be used with --transfer-size)",
    )
    options.add_argument(
        "--transfer-size", default=None, metavar="SIZE",
        help="Total data to send, such as 1G or 500M",
    )
    options.add_argument(
        "--warmup", default=None, metavar="SECONDS",
        help="Warmup time before measurement (default: 5; duration tests only)",
    )
    options.add_argument(
        "--threads", default=DEFAULT_THREADS, metavar="start[:end[:step]]",
        help="Thread counts (default: 1). Examples: 4; 1:4; 1:5:2",
    )
    options.add_argument(
        "--timeout", default=DEFAULT_TIMEOUT, metavar="SECONDS",
        help="Kubernetes wait timeout (default: 120)",
    )
    parser.set_defaults(run_id=f"run-{utc_timestamp('%Y%m%d-%H%M%S')}-{os.getpid()}")
    options.add_argument(
        "--keep-resources", action="store_true",
        help="Keep benchmark pods and service after completion",
    )
    options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit",
    )
    return parser


def parse_arguments(arguments: Sequence[str]) -> ParsedConfig:
    parsed = make_parser().parse_args(list(arguments))
    duration_value = parsed.duration
    return ParsedConfig(
        client_node=parsed.client,
        server_node=parsed.server,
        image=parsed.image,
        namespace=parsed.namespace,
        duration_text=duration_value or DEFAULT_DURATION,
        duration_set=duration_value is not None,
        warmup_text=parsed.warmup or DEFAULT_WARMUP,
        warmup_set=parsed.warmup is not None,
        transfer_size=parsed.transfer_size,
        threads_text=parsed.threads,
        timeout_text=parsed.timeout,
        keep_resources=parsed.keep_resources,
        run_id=parsed.run_id,
        log_dir=parsed.log_dir,
    )


def is_positive_integer(value: str) -> bool:
    """Return whether value is decimal, convertible, and greater than zero."""

    if not value or not value.isascii() or not value.isdecimal():
        return False
    try:
        return int(value) > 0
    except ValueError:
        return False


def is_nonnegative_integer(value: str) -> bool:
    """Return whether value is decimal and convertible to a non-negative integer."""

    if not value or not value.isascii() or not value.isdecimal():
        return False
    try:
        return int(value) >= 0
    except ValueError:
        return False


def parse_thread_spec(value: str) -> ThreadSpec | None:
    """Parse start[:end[:step]] into an inclusive thread-count range."""

    parts = value.split(":")
    if len(parts) not in (1, 2, 3) or any(
        not is_positive_integer(part) for part in parts
    ):
        return None

    start = int(parts[0])
    end = start if len(parts) == 1 else int(parts[1])
    step = 1 if len(parts) < 3 else int(parts[2])
    if start > end:
        return None
    return ThreadSpec(start=start, end=end, step=step)


def _valid_dns_subdomain(value: str, max_length: int) -> bool:
    if len(value) > max_length:
        return False
    labels = value.split(".")
    return all(
        label
        and len(label) <= 63
        and re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", label) is not None
        for label in labels
    )


def _valid_dns_label(value: str, max_length: int) -> bool:
    return (
        len(value) <= max_length
        and re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", value) is not None
    )


def validate_initial_config(config: ParsedConfig, colors: Colors) -> None:
    if not config.client_node:
        argument_error("--client is required", colors)
    if not config.server_node:
        argument_error("--server is required", colors)
    if config.client_node == config.server_node:
        argument_error("client and server nodes must be different", colors)
    if not MANIFEST_TEMPLATE.is_file():
        fail(f"manifest not found: {MANIFEST_TEMPLATE}", colors)
    if shutil.which("kubectl") is None:
        fail("kubectl is required", colors)


def validate_config(config: ParsedConfig, namespace: str, colors: Colors) -> BenchmarkConfig:
    if not is_positive_integer(config.duration_text):
        argument_error("--duration must be a positive integer", colors)
    if not is_nonnegative_integer(config.warmup_text):
        argument_error("--warmup must be a non-negative integer", colors)
    thread_spec = parse_thread_spec(config.threads_text)
    if thread_spec is None:
        argument_error(
            "--threads must use start[:end[:step]] with positive values and start <= end",
            colors,
        )
    if not is_positive_integer(config.timeout_text):
        argument_error("--timeout must be a positive integer", colors)
    if config.duration_set and config.transfer_size is not None:
        argument_error("--duration and --transfer-size cannot be used together", colors)
    if config.warmup_set and config.transfer_size is not None:
        argument_error("--warmup requires --duration", colors)

    transfer_size = config.transfer_size
    if transfer_size is not None:
        if re.fullmatch(r"[0-9KMGTP]+", transfer_size) is None:
            argument_error("--transfer-size must look like 1G, 500M, or 100K", colors)
        elif transfer_size[-1] in "KMGTP":
            if not is_positive_integer(transfer_size[:-1]):
                argument_error("--transfer-size must be a positive size", colors)
        elif not is_positive_integer(transfer_size):
            argument_error("--transfer-size must be a positive size", colors)

    for node_option, node_name in (
        ("--client", config.client_node),
        ("--server", config.server_node),
    ):
        if not _valid_dns_subdomain(node_name, 253):
            argument_error(f"{node_option} must be a valid DNS subdomain", colors)

    if not _valid_dns_label(namespace, 63):
        argument_error("--namespace must be a valid Kubernetes namespace", colors)
    if not _valid_dns_label(config.run_id, RUN_ID_LIMIT):
        argument_error("--run-id must be a lowercase DNS label of 38 characters or fewer", colors)
    if re.fullmatch(r"[A-Za-z0-9._/@:-]+", config.image) is None:
        argument_error("--image contains unsupported characters", colors)

    return BenchmarkConfig(
        client_node=config.client_node,
        server_node=config.server_node,
        image=config.image,
        namespace=namespace,
        duration=int(config.duration_text),
        duration_text=config.duration_text,
        duration_set=config.duration_set or transfer_size is None,
        warmup=int(config.warmup_text),
        warmup_text=config.warmup_text,
        transfer_size=transfer_size,
        threads=thread_spec.start,
        threads_text=config.threads_text,
        thread_spec=thread_spec,
        timeout=int(config.timeout_text),
        timeout_text=config.timeout_text,
        keep_resources=config.keep_resources,
        run_id=config.run_id,
        log_dir=config.log_dir,
    )
