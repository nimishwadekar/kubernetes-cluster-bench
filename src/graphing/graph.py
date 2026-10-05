from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Sequence

import matplotlib.pyplot as plt

from ..common.console import warning
from .data import BenchmarkRecord


class XParameter(StrEnum):
    TRANSFER_SIZE = "transfer_size"
    DURATION = "duration"
    PARALLEL_STREAMS = "parallel_streams"

    def label(self) -> str:
        if self is XParameter.TRANSFER_SIZE:
            return "Transfer size"
        if self is XParameter.DURATION:
            return "Duration (seconds)"
        if self is XParameter.PARALLEL_STREAMS:
            return "Parallel TCP streams"
        raise ValueError(f"unsupported X-axis parameter: {self}")


class YMetric(StrEnum):
    THROUGHPUT_GBPS = "throughput_gbps"
    RECEIVED_BYTES_GIB = "received_bytes_gib"
    TRANSFER_DURATION_SECONDS = "transfer_duration_seconds"
    RETRANSMITS = "retransmits"
    CLIENT_CPU_UTILIZATION = "client_cpu_utilization"
    SERVER_CPU_UTILIZATION = "server_cpu_utilization"

    def label(self) -> str:
        if self is YMetric.THROUGHPUT_GBPS:
            return "Receiver throughput (Gbit/s)"
        if self is YMetric.RECEIVED_BYTES_GIB:
            return "Received data (GiB)"
        if self is YMetric.TRANSFER_DURATION_SECONDS:
            return "Transfer duration (seconds)"
        if self is YMetric.RETRANSMITS:
            return "TCP retransmissions"
        if self is YMetric.CLIENT_CPU_UTILIZATION:
            return "Client CPU utilization (%)"
        if self is YMetric.SERVER_CPU_UTILIZATION:
            return "Server CPU utilization (%)"
        raise ValueError(f"unsupported Y-axis metric: {self}")


@dataclass(frozen=True)
class GraphContext:
    x_parameter: XParameter
    y_metric: YMetric
    output_path: Path
    title: str | None = None


def _x_value(record: BenchmarkRecord, parameter: XParameter) -> tuple[float | None, str]:
    metadata = record.metadata
    if parameter is XParameter.TRANSFER_SIZE:
        return metadata.transfer_size_bytes, metadata.transfer_size or "N/A"
    if parameter is XParameter.DURATION:
        value = metadata.duration_seconds
        return (float(value), str(value)) if value is not None else (None, "N/A")
    if parameter is XParameter.PARALLEL_STREAMS:
        value = metadata.parallel_streams
        return float(value), str(value)
    raise ValueError(f"unsupported X-axis parameter: {parameter}")


def _y_value(record: BenchmarkRecord, metric: YMetric) -> float | None:
    client = record.client
    if metric is YMetric.THROUGHPUT_GBPS:
        return (
            client.receiver_throughput_bps / 1e9
            if client.receiver_throughput_bps is not None
            else None
        )
    if metric is YMetric.RECEIVED_BYTES_GIB:
        return client.received_bytes / 2**30 if client.received_bytes is not None else None
    if metric is YMetric.TRANSFER_DURATION_SECONDS:
        return client.test_duration_seconds
    if metric is YMetric.RETRANSMITS:
        return float(client.retransmits) if client.retransmits is not None else None
    if metric is YMetric.CLIENT_CPU_UTILIZATION:
        return client.host_cpu_percent
    if metric is YMetric.SERVER_CPU_UTILIZATION:
        return client.remote_cpu_percent
    raise ValueError(f"unsupported Y-axis metric: {metric}")


def _run_id_lines(run_ids: Sequence[str]) -> str:
    return "\n".join(f"  - {run_id}" for run_id in run_ids)


def plot_benchmarks(records: Sequence[BenchmarkRecord], context: GraphContext) -> Path:
    points: list[tuple[float, str, float]] = []
    x_missing_run_ids: list[str] = []
    y_missing_run_ids: list[str] = []
    non_finite_run_ids: list[str] = []

    for record in records:
        y_value = _y_value(record, context.y_metric)
        x_value, x_label = _x_value(record, context.x_parameter)
        if x_value is None:
            x_missing_run_ids.append(record.metadata.run_id)
        if y_value is None:
            y_missing_run_ids.append(record.metadata.run_id)
        if y_value is not None and not math.isfinite(y_value):
            non_finite_run_ids.append(record.metadata.run_id)
        if x_value is None or y_value is None or not math.isfinite(y_value):
            continue
        points.append((x_value, x_label, y_value))

    if x_missing_run_ids:
        warning(
            f"X-axis {context.x_parameter.value} is N/A for "
            f"{len(x_missing_run_ids)} run(s):\n{_run_id_lines(x_missing_run_ids)}"
        )
    if y_missing_run_ids:
        warning(
            f"Y-axis {context.y_metric.value} is N/A for "
            f"{len(y_missing_run_ids)} run(s):\n{_run_id_lines(y_missing_run_ids)}"
        )
    if non_finite_run_ids:
        warning(
            f"Y-axis {context.y_metric.value} is non-finite for run(s):\n"
            f"{_run_id_lines(non_finite_run_ids)}"
        )

    if len(x_missing_run_ids) == len(records):
        raise ValueError(f"all runs have N/A values for X-axis {context.x_parameter.value}")
    if len(y_missing_run_ids) == len(records):
        raise ValueError(f"all runs have N/A values for Y-axis {context.y_metric.value}")
    if not points:
        raise ValueError("no finite benchmark results contain the selected metrics")

    points.sort(key=lambda point: point[0])
    x_values = [point[0] for point in points]
    labels = [point[1] for point in points]
    y_values = [point[2] for point in points]

    figure = plt.figure(figsize=(10, 6))
    axes = figure.gca()
    axes.plot(x_values, y_values, marker="o", linewidth=1.5, markersize=6)
    highest_y = max(y_values)
    y_padding = highest_y * 0.1 if highest_y > 0 else 1.0
    axes.set_ylim(top=highest_y + y_padding)
    axes.set_xlabel(context.x_parameter.label())
    axes.set_ylabel(context.y_metric.label())
    axes.set_title(context.title or f"{context.y_metric.label()} by {context.x_parameter.label()}")
    axes.grid(True, alpha=0.3)

    if context.x_parameter is XParameter.TRANSFER_SIZE:
        axes.set_xticks(x_values, labels)

    figure.tight_layout()
    context.output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(context.output_path, dpi=150)
    plt.close(figure)
    return context.output_path
