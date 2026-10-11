from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Sequence

from ...graphing.contracts import AnalysisRequest, GraphPoint, Observation, ResultAnalyzer
from ...graphing.selection import numeric_dimension, require_dimensions, select_observations
from ...graphing.statistics import mean, standard_deviation
from .parser import NixlParser


class NixlAnalyzer(ResultAnalyzer):
    """Aggregate NIXL table rows across repeated external runs."""

    def __init__(self, parser: NixlParser | None = None) -> None:
        self.parser = parser or NixlParser()

    def discover(self, raw_directory: Path) -> tuple[Path, ...]:
        return self.parser.discover(raw_directory)

    def parse(self, run_directory: Path) -> tuple[Observation, ...]:
        return self.parser.parse(run_directory)

    def aggregate(
        self,
        observations: tuple[Observation, ...],
        request: AnalysisRequest,
    ) -> tuple[GraphPoint, ...]:
        selected = select_observations(observations, request)
        require_dimensions(selected, request.series_dimensions)
        varying_hidden: set[str] = set()
        dimensions = {
            name
            for observation in selected
            for name in observation.dimensions
        }
        for dimension in dimensions:
            if dimension == request.x_dimension or dimension in request.series_dimensions:
                continue
            values = {observation.dimensions.get(dimension) for observation in selected}
            if len(values) > 1:
                varying_hidden.add(dimension)
        if varying_hidden:
            names = ", ".join(sorted(varying_hidden))
            raise ValueError(
                f"NIXL dimensions vary ({names}); add --filter or --series"
            )

        groups: dict[tuple[float, tuple[str, ...]], list[float]] = defaultdict(list)
        labels: dict[tuple[float, tuple[str, ...]], str] = {}
        for observation in selected:
            x_value = numeric_dimension(observation, request.x_dimension)
            series_key = tuple(
                f"{dimension}={observation.dimensions[dimension]}"
                for dimension in request.series_dimensions
            )
            group_key = (x_value, series_key)
            groups[group_key].append(observation.value / 1e9)
            labels[group_key] = _x_label(observation, request.x_dimension, x_value)

        points: list[GraphPoint] = []
        for group_key, values in groups.items():
            x_value, series_key = group_key
            error = standard_deviation(values) if len(values) > 1 else 0.0
            points.append(
                GraphPoint(
                    x_value=x_value,
                    y_value=mean(values),
                    x_label=labels[group_key],
                    series_key=series_key,
                    y_error=(error, error),
                )
            )
        points.sort(key=lambda point: (point.x_value, point.series_key))
        return tuple(points)


def _x_label(observation: Observation, dimension: str, value: float) -> str:
    if dimension == "block_size_bytes":
        return str(int(value))
    return str(int(value)) if value.is_integer() else str(value)
