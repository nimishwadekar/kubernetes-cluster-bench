from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn, Sequence, TextIO

from ..common.console import Colors


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_TEMPLATE = PROJECT_ROOT / "network-benchmark.yaml"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_IMAGE = "quay.io/nwadekar/kubernetes-cluster-bench:latest"
DEFAULT_DURATION = "10"
DEFAULT_PARALLEL_STREAMS = "1"
DEFAULT_TIMEOUT = "120"
RUN_ID_LIMIT = 38
PROGRESS_WIDTH = 30

VALUE_OPTIONS: tuple[str, ...] = (
    "-c",
    "--client",
    "-s",
    "--server",
    "--image",
    "-l",
    "--logs-dir",
    "-n",
    "--namespace",
    "--duration",
    "--transfer-size",
    "--parallel",
    "--timeout",
    "--run-id",
)

USAGE: str = """
Usage:
  ./run-benchmark.sh --client NODE --server NODE [options]

Required:
  -c, --client NODE        Kubernetes node for the client pod
  -s, --server NODE        Kubernetes node for the server pod

Options:
  --image IMAGE            Benchmark image (default: quay.io/nwadekar/kubernetes-cluster-bench:latest)
  -l, --logs-dir DIR       Directory for benchmark logs (default: repository logs directory)
  -n, --namespace NAMESPACE Kubernetes namespace (default: current context namespace)
  --duration SECONDS       Test duration (default: 10; ignored with --transfer-size)
  --transfer-size SIZE     Total data to send, such as 1G or 500M
  --parallel STREAMS       Number of parallel iperf3 streams (default: 1)
  --timeout SECONDS        Kubernetes wait timeout (default: 120)
  --run-id ID              Identifier used in pod and log names
  --keep-resources         Keep benchmark pods and service after completion
  -h, --help               Show this help
"""


@dataclass(frozen=True)
class ParsedConfig:
    """Raw command-line values retained until validation."""

    client_node: str
    server_node: str
    image: str
    namespace: str
    duration_text: str
    duration_set: bool
    transfer_size: str | None
    parallel_streams_text: str
    timeout_text: str
    keep_resources: bool
    run_id: str
    log_dir: str


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
    transfer_size: str | None
    parallel_streams: int
    parallel_streams_text: str
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


class RunnerArgumentParser(argparse.ArgumentParser):
    """Argument parser that preserves the wrapper's help and error surface."""

    def __init__(self, colors: Colors) -> None:
        super().__init__(add_help=False, allow_abbrev=False)
        self.colors = colors

    def format_help(self) -> str:
        return USAGE

    def error(self, message: str) -> NoReturn:
        prefix = "unrecognized arguments: "
        if message.startswith(prefix):
            unknown = message[len(prefix) :].split()[0]
            argument_error(f"unknown argument: {unknown}", self.colors)
        argument_error(message, self.colors)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        del message
        raise BenchmarkExit(0 if status == 0 else status)


def usage(stream: TextIO) -> None:
    """Print the command usage text."""

    print(USAGE, file=stream, end="")


def fail(message: str, colors: Colors) -> NoReturn:
    """Print a non-argument error and return shell status two."""

    print(f"{colors.red}ERROR:{colors.reset} {message}", file=sys.stderr)
    raise BenchmarkExit(2)


def argument_error(message: str, colors: Colors) -> NoReturn:
    """Print an argument error, usage, and return shell status two."""

    print(f"{colors.red}ERROR:{colors.reset} {message}", file=sys.stderr)
    usage(sys.stderr)
    raise BenchmarkExit(2)


def validate_option_values(arguments: Sequence[str], colors: Colors) -> None:
    """Reject missing option values before argparse produces its own wording."""

    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option in ("-h", "--help"):
            return
        if option == "--":
            argument_error("unknown argument: --", colors)
        if option in VALUE_OPTIONS:
            if index + 1 >= len(arguments):
                argument_error(f"missing value for {option}", colors)
            value = arguments[index + 1]
            if not value:
                argument_error(f"empty value for {option}", colors)
            if value.startswith("-"):
                argument_error(f"missing value for {option}", colors)
            index += 2
            continue
        if option == "--keep-resources":
            index += 1
            continue
        argument_error(f"unknown argument: {option}", colors)


def utc_timestamp(format_string: str) -> str:
    return datetime.now(timezone.utc).strftime(format_string)


def make_parser(colors: Colors) -> RunnerArgumentParser:
    parser = RunnerArgumentParser(colors)
    parser.add_argument("-c", "--client", default="")
    parser.add_argument("-s", "--server", default="")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("-l", "--logs-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("-n", "--namespace", default="")
    parser.add_argument("--duration", default=DEFAULT_DURATION)
    parser.add_argument("--transfer-size", default=None)
    parser.add_argument("--parallel", default=DEFAULT_PARALLEL_STREAMS)
    parser.add_argument("--timeout", default=DEFAULT_TIMEOUT)
    parser.add_argument("--run-id", default=f"run-{utc_timestamp('%Y%m%d-%H%M%S')}-{os.getpid()}")
    parser.add_argument("--keep-resources", action="store_true", default=False)
    parser.add_argument("-h", "--help", action="help")
    return parser


def parse_arguments(arguments: Sequence[str], colors: Colors) -> ParsedConfig:
    validate_option_values(arguments, colors)
    parser = make_parser(colors)
    parsed, extras = parser.parse_known_args(list(arguments))
    if extras:
        argument_error(f"unknown argument: {extras[0]}", colors)

    transfer_value = getattr(parsed, "transfer_size")
    return ParsedConfig(
        client_node=str(getattr(parsed, "client")),
        server_node=str(getattr(parsed, "server")),
        image=str(getattr(parsed, "image")),
        namespace=str(getattr(parsed, "namespace")),
        duration_text=str(getattr(parsed, "duration")),
        duration_set="--duration" in arguments,
        transfer_size=None if transfer_value is None else str(transfer_value),
        parallel_streams_text=str(getattr(parsed, "parallel")),
        timeout_text=str(getattr(parsed, "timeout")),
        keep_resources=bool(getattr(parsed, "keep_resources")),
        run_id=str(getattr(parsed, "run_id")),
        log_dir=str(getattr(parsed, "logs_dir")),
    )


def is_positive_integer(value: str) -> bool:
    """Return whether value is decimal, convertible, and greater than zero."""

    if not value or not value.isascii() or not value.isdecimal():
        return False
    try:
        return int(value) > 0
    except ValueError:
        return False


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
    if not is_positive_integer(config.parallel_streams_text):
        argument_error("--parallel must be a positive integer", colors)
    if not is_positive_integer(config.timeout_text):
        argument_error("--timeout must be a positive integer", colors)
    if config.duration_set and config.transfer_size is not None:
        argument_error("--duration and --transfer-size cannot be used together", colors)

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
        transfer_size=transfer_size,
        parallel_streams=int(config.parallel_streams_text),
        parallel_streams_text=config.parallel_streams_text,
        timeout=int(config.timeout_text),
        timeout_text=config.timeout_text,
        keep_resources=config.keep_resources,
        run_id=config.run_id,
        log_dir=config.log_dir,
    )
