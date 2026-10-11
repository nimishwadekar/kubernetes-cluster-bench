from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import NoReturn, Sequence

from ..backends.registry import BackendRegistry, default_registry
from ..common.console import format_error
from .contracts import AnalysisRequest, DimensionValue
from .formatting import GraphArgumentParser, GraphHelpFormatter
from .loader import load_observations
from .renderer import GraphRenderConfig, render_graph


THROUGHPUT_METRIC = "throughput"


def build_parser() -> GraphArgumentParser:
    parser = GraphArgumentParser(
        prog="plot-benchmark.sh",
        usage="%(prog)s --backend BACKEND --name NAME --x DIMENSION [options]",
        description="Plot benchmark throughput from backend raw artifacts.",
        formatter_class=GraphHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument("--backend", required=True, metavar="BACKEND")
    parser.add_argument(
        "--name",
        required=True,
        metavar="NAME",
        help="Benchmark log directory name.",
    )
    parser.add_argument("-d", "--dir", dest="directory", type=Path, default=Path("logs"))
    parser.add_argument("--x", required=True, dest="x_dimension", metavar="DIMENSION")
    parser.add_argument(
        "--y",
        default=THROUGHPUT_METRIC,
        choices=(THROUGHPUT_METRIC,),
        metavar="METRIC",
        help="Metric to plot (currently only throughput).",
    )
    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            "Fix a dimension to one value; use for dimensions that are not "
            "the X axis or a series. May be repeated."
        ),
    )
    parser.add_argument(
        "--series",
        action="append",
        default=[],
        dest="series_dimensions",
        metavar="DIMENSION",
        help=(
            "Plot a separate line for each value of this dimension; use instead "
            "of filtering when comparison is desired. May be repeated."
        ),
    )
    parser.add_argument("--percentile", type=percentile, default=None, metavar="PERCENT")
    parser.add_argument("--max-throughput", type=positive_float, default=None, metavar="GBIT/S")
    parser.add_argument("--output", type=Path, default=None, metavar="FILE")
    parser.add_argument("--title", default=None, metavar="TEXT")
    return parser


def main(
    arguments: Sequence[str] | None = None,
    registry: BackendRegistry | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(arguments)
    selected_registry = registry or default_registry()
    try:
        x_dimension = _canonical_dimension(args.x_dimension)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.name) is None:
            raise ValueError(
                "name must contain only letters, numbers, dots, underscores, and hyphens"
            )
        request = AnalysisRequest(
            metric=args.y,
            x_dimension=x_dimension,
            filters=_parse_filters(args.filter),
            series_dimensions=tuple(args.series_dimensions),
            percentile=args.percentile,
        )
        test_directory = args.directory / args.backend / args.name
        observations = load_observations(
            args.backend,
            test_directory,
            selected_registry,
        )
        analyzer = selected_registry.get(args.backend).create_analyzer()
        points = analyzer.aggregate(observations, request)
        if not points:
            raise ValueError("no finite observations contain the selected options")
        output_path = args.output or (
            test_directory / "graphs" / f"throughput-{args.x_dimension}.png"
        )
        render_graph(
            points,
            GraphRenderConfig(
                output_path=output_path,
                x_label=_label(args.x_dimension),
                y_label="Throughput (Gbit/s)",
                title=args.title or f"Throughput by {_label(args.x_dimension)}",
                reference_y=args.max_throughput,
                reference_label=(
                    f"{args.max_throughput:g} Gbit/s max"
                    if args.max_throughput is not None
                    else None
                ),
            ),
        )
        print(f"Graph written to {output_path}")
    except (OSError, RuntimeError, ValueError) as error:
        print(format_error(str(error), sys.stderr), file=sys.stderr)
        return 2
    return 0


def percentile(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not 0 <= parsed <= 100:
        raise argparse.ArgumentTypeError("must be between 0 and 100")
    return parsed


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _parse_filters(values: Sequence[str]) -> dict[str, DimensionValue]:
    filters: dict[str, DimensionValue] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--filter must use NAME=VALUE")
        name, raw_value = value.split("=", 1)
        if not name or not raw_value:
            raise ValueError("--filter must have a non-empty name and value")
        filters[name] = _parse_scalar(raw_value)
    return filters


def _parse_scalar(value: str) -> DimensionValue:
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _canonical_dimension(dimension: str) -> str:
    if dimension == "transfer_size":
        return "transfer_size_bytes"
    return dimension


def _label(dimension: str) -> str:
    if dimension == "transfer_size_bytes":
        return "Transfer size"
    return dimension.replace("_", " ").capitalize()
