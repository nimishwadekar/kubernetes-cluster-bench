from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn, Sequence

from ..common.console import colored_options, format_error, warning
from .data import BenchmarkRecord, load_benchmark_record
from .graph import GraphContext, XParameter, YMetric, plot_benchmarks


def usage() -> str:
    return (
        "%(prog)s --x PARAM --y METRIC [--logs-dir DIR]\n"
        "       [--output FILE] [--title TITLE]"
    )


class BenchmarkHelpFormatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=32, width=100)


class UsageArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        print(format_error(message, sys.stderr), file=sys.stderr)
        print(file=sys.stderr)
        self.print_help(sys.stderr)
        self.exit(2)


def build_parser() -> UsageArgumentParser:
    parser = UsageArgumentParser(
        prog="plot-benchmark.sh",
        usage=usage(),
        description="Plot throughput and other metrics from Kubernetes benchmark JSON files.",
        formatter_class=BenchmarkHelpFormatter,
    )
    parser.add_argument(
        "--x",
        dest="x_parameter",
        type=XParameter,
        choices=tuple(XParameter),
        required=True,
        metavar="PARAM",
        help=(
            "Parameter for the X axis. Options: "
            + colored_options(tuple(option.value for option in XParameter), sys.stdout)
            + "."
        ),
    )
    parser.add_argument(
        "--y",
        dest="y_metric",
        type=YMetric,
        choices=tuple(YMetric),
        required=True,
        metavar="METRIC",
        help=(
            "Metric for the Y axis. Options: "
            + colored_options(tuple(metric.value for metric in YMetric), sys.stdout)
            + "."
        ),
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=Path("logs"),
        metavar="DIR",
        help="Directory containing benchmark metadata JSON files (default: logs).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="FILE",
        help="Output image path (default: <logs-dir>/graphs/benchmark.png).",
    )
    parser.add_argument(
        "--title",
        metavar="TEXT",
        help="Optional graph title."
    )

    return parser


def metadata_paths(args: argparse.Namespace) -> list[Path]:
    logs_dir: Path = args.logs_dir
    return sorted(logs_dir.glob("*-metadata.json"))


def load_records(paths: Sequence[Path]) -> list[BenchmarkRecord]:
    if not paths:
        raise ValueError("no metadata JSON files were found")
    records: list[BenchmarkRecord] = []
    for path in paths:
        records.append(load_benchmark_record(path))
    return records


def validate_records(records: Sequence[BenchmarkRecord]) -> None:
    unsuccessful: list[str] = []
    non_finite_labels = {
        "host_cpu_percent": "client_cpu_utilization",
        "remote_cpu_percent": "server_cpu_utilization",
    }
    for record in records:
        if record.metadata.trailing_content:
            warning(f"trailing JSON content ignored in {record.metadata.source_path}")
        if record.client.trailing_content:
            warning(f"trailing JSON content ignored in {record.client.source_path}")
        if record.client.non_finite_fields:
            fields = ", ".join(
                non_finite_labels.get(field, field)
                for field in record.client.non_finite_fields
            )
            warning(f"non-finite value(s) in {fields} for run {record.metadata.run_id}")

        if record.metadata.client_status != 0:
            status = record.metadata.client_status
            status_text = "missing" if status is None else str(status)
            unsuccessful.append(f"{record.metadata.run_id} (client_status={status_text})")
        elif record.client.error is not None:
            unsuccessful.append(f"{record.metadata.run_id} ({record.client.error})")
        elif not record.client.successful:
            unsuccessful.append(f"{record.metadata.run_id} (incomplete client result)")

    if unsuccessful:
        raise ValueError("unsuccessful benchmark run(s): " + ", ".join(unsuccessful))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        records = load_records(metadata_paths(args))
        validate_records(records)
        logs_dir: Path = args.logs_dir
        output_path: Path = args.output or logs_dir / "graphs" / "benchmark.png"
        context = GraphContext(
            x_parameter=args.x_parameter,
            y_metric=args.y_metric,
            output_path=output_path,
            title=args.title,
        )
        output_path = plot_benchmarks(records, context)
    except (OSError, ValueError) as error:
        print(format_error(str(error), sys.stderr), file=sys.stderr)
        return 2
    print(f"Graph written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
