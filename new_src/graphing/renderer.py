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

    figure = plt.figure(figsize=(10, 6))
    axes = figure.gca()
    for series_key, group in groups.items():
        ordered = sorted(group, key=lambda point: point.x_value)
        x_values = [point.x_value for point in ordered]
        y_values = [point.y_value for point in ordered]
        label = ", ".join(series_key) if series_key else None
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
    if any(point.series_key for point in points):
        axes.legend()

    figure.tight_layout()
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(config.output_path, dpi=150)
    plt.close(figure)
    return config.output_path
