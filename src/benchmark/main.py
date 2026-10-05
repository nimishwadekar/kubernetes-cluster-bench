from __future__ import annotations

import subprocess
import sys
from typing import Sequence

from ..common.console import terminal_colors
from .config import (
    BenchmarkExit,
    ParsedConfig,
    parse_arguments,
    validate_config,
    validate_initial_config,
)
from .execution import run_benchmark
from .kubernetes import KubectlError, populate_context_namespace


def main(arguments: Sequence[str]) -> int:
    """Parse arguments and run without deploying during module import."""

    colors = terminal_colors(sys.stdout, sys.stderr)
    try:
        parsed: ParsedConfig = parse_arguments(arguments)
        validate_initial_config(parsed, colors)
        kube_context, namespace = populate_context_namespace(parsed.namespace)
        config = validate_config(parsed, namespace, colors)
        return run_benchmark(config, kube_context, colors)
    except SystemExit as error:
        return int(error.code) if isinstance(error.code, int) else 1
    except BenchmarkExit as error:
        return error.code
    except subprocess.CalledProcessError as error:
        return error.returncode if error.returncode > 0 else 1
    except (KubectlError, OSError, ValueError) as error:
        print(f"{colors.red}ERROR:{colors.reset} {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
