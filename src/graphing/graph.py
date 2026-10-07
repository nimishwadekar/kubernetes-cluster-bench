from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Sequence

import matplotlib.pyplot as plt

from ..common.console import warning
from .data import BenchmarkRecord


class XParameter(StrEnum):
    TRANSFER_SIZE = "transfer_size"
    THREADS = "threads"

    def label(self) -> str:
        if self is XParameter.TRANSFER_SIZE:
            return "Transfer size"
        if self is XParameter.THREADS:
            return "Threads"
        raise ValueError(f"unsupported X-axis parameter: {self}")


class YMetric(StrEnum):
    THROUGHPUT = "throughput"
    RETRANSMITS = "retransmits"
    CLIENT_CPU_UTIL = "client_cpu_util"

    def label(self) -> str:
        if self is YMetric.THROUGHPUT:
            return "Receiver throughput (Gbit/s)"
        if self is YMetric.RETRANSMITS:
            return "TCP retransmissions"
        if self is YMetric.CLIENT_CPU_UTIL:
            return "Client CPU utilization (%)"
        raise ValueError(f"unsupported Y-axis metric: {self}")


@dataclass(frozen=True)
class GraphContext:
    x_parameter: XParameter
    y_metric: YMetric
    output_path: Path
    title: str | None = None
    max_throughput_gbps: float | None = None
    percentile: float | None = None


@dataclass(frozen=True)
class _GraphPoint:
    x_value: float
    x_label: str
    y_value: float
    y_error: tuple[float, float] | None
    group_key: tuple[str, float, str]


def _x_value(record: BenchmarkRecord, parameter: XParameter) -> tuple[float | None, str]:
    metadata = record.metadata
    if parameter is XParameter.TRANSFER_SIZE:
        return metadata.transfer_size_bytes, metadata.transfer_size or "N/A"
    if parameter is XParameter.THREADS:
        value = metadata.threads
        return float(value), str(value)
    raise ValueError(f"unsupported X-axis parameter: {parameter}")


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile without values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def format_percentile(percentile: float) -> str:
    return f"{percentile:g}"


def _y_value_and_error(
    record: BenchmarkRecord,
    metric: YMetric,
    percentile: float | None = None,
) -> tuple[float | None, tuple[float, float] | None]:
    client = record.client
    if metric is YMetric.THROUGHPUT:
        if percentile is None:
            mean_bps = client.server_throughput_mean_bps
        elif client.server_throughput_values_bps:
            mean_bps = _percentile(client.server_throughput_values_bps, percentile)
        else:
            mean_bps = None
        standard_deviation_bps = client.server_throughput_stddev_bps
        if mean_bps is None or standard_deviation_bps is None:
            return None, None
        standard_deviation = standard_deviation_bps / 1e9
        return mean_bps / 1e9, (standard_deviation, standard_deviation)
    if metric is YMetric.RETRANSMITS:
        value = float(client.retransmits) if client.retransmits is not None else None
        return value, None
    if metric is YMetric.CLIENT_CPU_UTIL:
        return client.host_cpu_percent, None
    raise ValueError(f"unsupported Y-axis metric: {metric}")


def _aggregate_run_points(
    points: Sequence[_GraphPoint], metric: YMetric
) -> list[_GraphPoint]:
    grouped: dict[tuple[str, float, str], list[_GraphPoint]] = {}
    for point in points:
        grouped.setdefault(point.group_key, []).append(point)

    aggregated: list[_GraphPoint] = []
    for group in grouped.values():
        if len(group) == 1:
            aggregated.append(group[0])
            continue

        y_values = [point.y_value for point in group]
        mean = statistics.fmean(y_values)
        if metric is YMetric.THROUGHPUT:
            standard_deviation = statistics.stdev(y_values)
            y_error = (standard_deviation, standard_deviation)
        else:
            y_error = None
        first = group[0]
        aggregated.append(
            _GraphPoint(
                x_value=first.x_value,
                x_label=first.x_label,
                y_value=mean,
                y_error=y_error,
                group_key=first.group_key,
            )
        )
    return aggregated


def _test_id_lines(test_ids: Sequence[str]) -> str:
    return "\n".join(f"  - {test_id}" for test_id in test_ids)


def plot_benchmarks(records: Sequence[BenchmarkRecord], context: GraphContext) -> Path:
    if context.max_throughput_gbps is not None and context.y_metric is not YMetric.THROUGHPUT:
        raise ValueError("--max-throughput can only be used with throughput graphs")
    if context.percentile is not None and context.y_metric is not YMetric.THROUGHPUT:
        raise ValueError("--percentile can only be used with throughput graphs")

    points: list[_GraphPoint] = []
    x_missing_test_ids: list[str] = []
    y_missing_test_ids: list[str] = []
    non_finite_test_ids: list[str] = []

    for record in records:
        y_value, y_error = _y_value_and_error(
            record, context.y_metric, context.percentile
        )
        x_value, x_label = _x_value(record, context.x_parameter)
        y_is_non_finite = y_value is not None and not math.isfinite(y_value)
        if y_error is not None:
            y_is_non_finite = y_is_non_finite or not all(
                math.isfinite(error) for error in y_error
            )
        if x_value is None:
            x_missing_test_ids.append(record.metadata.test_id)
        if y_value is None:
            y_missing_test_ids.append(record.metadata.test_id)
        if y_is_non_finite:
            non_finite_test_ids.append(record.metadata.test_id)
        if x_value is None or y_value is None or y_is_non_finite:
            continue
        group_id = (
            record.metadata.test_id
            if record.metadata.run_number is not None
            else str(record.client.source_path)
        )
        points.append(
            _GraphPoint(
                x_value=x_value,
                x_label=x_label,
                y_value=y_value,
                y_error=y_error,
                group_key=(group_id, x_value, x_label),
            )
        )

    if x_missing_test_ids:
        warning(
            f"X-axis {context.x_parameter.value} is N/A for "
            f"{len(x_missing_test_ids)} test(s):\n{_test_id_lines(x_missing_test_ids)}"
        )
    if y_missing_test_ids:
        warning(
            f"Y-axis {context.y_metric.value} is N/A for "
            f"{len(y_missing_test_ids)} test(s):\n{_test_id_lines(y_missing_test_ids)}"
        )
    if non_finite_test_ids:
        warning(
            f"Y-axis {context.y_metric.value} is non-finite for test(s):\n"
            f"{_test_id_lines(non_finite_test_ids)}"
        )

    if len(x_missing_test_ids) == len(records):
        raise ValueError(f"all tests have N/A values for X-axis {context.x_parameter.value}")
    if len(y_missing_test_ids) == len(records):
        raise ValueError(f"all tests have N/A values for Y-axis {context.y_metric.value}")
    if not points:
        raise ValueError("no finite benchmark results contain the selected metrics")

    points = _aggregate_run_points(points, context.y_metric)
    points.sort(key=lambda point: point.x_value)
    x_values = [point.x_value for point in points]
    labels = [point.x_label for point in points]
    y_values = [point.y_value for point in points]

    figure = plt.figure(figsize=(10, 6))
    axes = figure.gca()
    if context.y_metric is YMetric.THROUGHPUT:
        lower_errors = [point.y_error[0] for point in points if point.y_error is not None]
        upper_errors = [point.y_error[1] for point in points if point.y_error is not None]
        axes.errorbar(
            x_values,
            y_values,
            yerr=[lower_errors, upper_errors],
            fmt="-o",
            linewidth=1.5,
            markersize=6,
            markerfacecolor="none",
            capsize=4,
        )
    else:
        axes.plot(
            x_values,
            y_values,
            marker="o",
            linewidth=1.5,
            markersize=6,
            markerfacecolor="none",
        )
    highest_y = max(
        max(
            point.y_value + (point.y_error[1] if point.y_error is not None else 0.0)
            for point in points
        ),
        context.max_throughput_gbps or 0.0,
    )
    y_padding = highest_y * 0.1 if highest_y > 0 else 1.0
    axes.set_ylim(bottom=0, top=highest_y + y_padding)
    axes.set_xlabel(context.x_parameter.label())
    axes.set_ylabel(context.y_metric.label())
    title = context.title or f"{context.y_metric.label()} by {context.x_parameter.label()}"
    if context.percentile is not None:
        title += f" - {format_percentile(context.percentile)}th percentile"
    axes.set_title(title)
    if context.max_throughput_gbps is not None:
        axes.axhline(
            context.max_throughput_gbps,
            color="tab:red",
            linestyle="--",
            linewidth=1.5,
        )
        axes.text(
            0.01,
            context.max_throughput_gbps,
            f"{context.max_throughput_gbps:g} Gbit/s max",
            transform=axes.get_yaxis_transform(),
            color="tab:red",
            ha="left",
            va="bottom",
        )
    axes.grid(True, alpha=0.3)

    if context.x_parameter is XParameter.TRANSFER_SIZE:
        axes.set_xticks(x_values, labels)
    elif context.x_parameter is XParameter.THREADS:
        axes.set_xlim(left=0)

    figure.tight_layout()
    context.output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(context.output_path, dpi=150)
    plt.close(figure)
    return context.output_path
