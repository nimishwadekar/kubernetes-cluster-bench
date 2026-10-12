from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Sequence

import matplotlib.pyplot as plt

from .contracts import GraphPoint


@dataclass(frozen=True)
class GraphRenderConfig:
    """Presentation settings shared by backend-specific analyzers."""

    output_path: Path
    x_label: str
    y_label: str
    title: str
    reference_y: float | None = None
    reference_label: str | None = None
    legend_label: str = "Throughput"
    x_log_base: float | None = None


def _series_sort_key(series_key: tuple[str, ...]) -> tuple[tuple[str, float, str], ...]:
    result: list[tuple[str, float, str]] = []
    for item in series_key:
        name, _, raw_value = item.partition("=")
        result.append((name, _sort_value(raw_value), raw_value))
    return tuple(result)


def _sort_value(value: str) -> float:
    suffix = value[-1:].upper()
    multiplier = {"K": 1024, "M": 1024**2, "G": 1024**3}.get(suffix, 1)
    number = value[:-1] if suffix in {"K", "M", "G"} else value
    try:
        return float(number) * multiplier
    except ValueError:
        return float("inf")


def render_graph(
    points: Sequence[GraphPoint],
    config: GraphRenderConfig,
) -> Path:
    """Render graph points without knowing their backend or sample semantics."""

    if not points:
        raise ValueError("cannot render a graph without points")
    if config.reference_y is not None and (
        not math.isfinite(config.reference_y) or config.reference_y <= 0
    ):
        raise ValueError("reference_y must be finite and positive")

    groups: dict[tuple[str, ...], list[GraphPoint]] = {}
    for point in points:
        groups.setdefault(point.series_key, []).append(point)

    figure = plt.figure(figsize=(14, 6))
    axes = figure.gca()
    ordered_groups = sorted(
        groups.items(), key=lambda item: _series_sort_key(item[0])
    )
    colors = plt.colormaps.get_cmap("turbo").resampled(len(ordered_groups))
    for index, (series_key, group) in enumerate(ordered_groups):
        color = colors(index)
        ordered = sorted(group, key=lambda point: point.x_value)
        x_values = [point.x_value for point in ordered]
        y_values = [point.y_value for point in ordered]
        label = ", ".join(series_key) if series_key else config.legend_label
        if any(point.y_error is not None for point in ordered):
            lower_errors = [
                point.y_error[0] if point.y_error is not None else 0.0
                for point in ordered
            ]
            upper_errors = [
                point.y_error[1] if point.y_error is not None else 0.0
                for point in ordered
            ]
            axes.errorbar(
                x_values,
                y_values,
                yerr=[lower_errors, upper_errors],
                fmt="-o",
                linewidth=1.5,
                markersize=6,
                markerfacecolor="none",
                capsize=4,
                label=label,
                color=color,
            )
        else:
            axes.plot(
                x_values,
                y_values,
                marker="o",
                linewidth=1.5,
                markersize=6,
                markerfacecolor="none",
                label=label,
                color=color,
            )

    highest_y = max(
        max(
            point.y_value
            + (point.y_error[1] if point.y_error is not None else 0.0)
            for point in points
        ),
        config.reference_y or 0.0,
    )
    y_padding = highest_y * 0.1 if highest_y > 0 else 1.0
    axes.set_ylim(bottom=0, top=highest_y + y_padding)
    if config.x_log_base is not None:
        if config.x_log_base <= 0 or config.x_log_base == 1:
            raise ValueError("logarithmic X-axis base must be positive and not 1")
        if any(point.x_value <= 0 for point in points):
            raise ValueError("log-scaled X axis requires positive values")
        axes.set_xscale("log", base=config.x_log_base)
    axes.set_xlabel(config.x_label)
    axes.set_ylabel(config.y_label)
    axes.set_title(config.title)
    x_labels: dict[float, str] = {}
    for point in points:
        existing = x_labels.get(point.x_value)
        if existing is not None and existing != point.x_label:
            x_labels = {}
            break
        x_labels[point.x_value] = point.x_label
    if x_labels:
        x_ticks = sorted(x_labels)
        axes.set_xticks(x_ticks, [x_labels[value] for value in x_ticks])
    axes.grid(True, alpha=0.3)
    if config.reference_y is not None:
        axes.axhline(
            config.reference_y,
            color="tab:red",
            linestyle="--",
            linewidth=1.5,
        )
        axes.text(
            0.01,
            config.reference_y,
            config.reference_label or f"{config.reference_y:g} max",
            transform=axes.get_yaxis_transform(),
            color="tab:red",
            ha="left",
            va="bottom",
        )
    axes.legend(
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        borderaxespad=0.0,
    )

    figure.tight_layout(rect=(0.0, 0.0, 0.8, 1.0))
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(config.output_path, dpi=150, bbox_inches="tight", pad_inches=0.1)
    plt.close(figure)
    return config.output_path
