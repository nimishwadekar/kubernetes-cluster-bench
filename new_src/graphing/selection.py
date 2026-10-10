from __future__ import annotations

import math
from typing import Sequence

from ..common.types import DimensionValue
from .contracts import AnalysisRequest, MissingDimensionError, Observation


def select_observations(
    observations: Sequence[Observation],
    request: AnalysisRequest,
) -> tuple[Observation, ...]:
    """Apply metric and dimension filters, requiring requested dimensions."""

    selected = tuple(
        observation
        for observation in observations
        if observation.metric == request.metric
    )
    if not selected:
        raise ValueError(f"no observations found for metric {request.metric!r}")
    required_dimensions = (request.x_dimension, *request.filters.keys())
    require_dimensions(selected, required_dimensions)
    for dimension, expected in request.filters.items():
        selected = tuple(
            observation
            for observation in selected
            if observation.dimensions[dimension] == expected
        )
    return selected


def require_dimensions(
    observations: Sequence[Observation],
    dimensions: Sequence[str],
) -> None:
    """Raise if any requested dimension is absent from any observation."""

    for dimension in dimensions:
        missing = tuple(
            index
            for index, observation in enumerate(observations)
            if dimension not in observation.dimensions
        )
        if missing:
            raise MissingDimensionError(dimension, missing)


def numeric_dimension(observation: Observation, dimension: str) -> float:
    """Read a finite numeric dimension for graphing."""

    if dimension not in observation.dimensions:
        raise MissingDimensionError(dimension, (0,))
    value: DimensionValue = observation.dimensions[dimension]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"dimension {dimension!r} must be numeric")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError(f"dimension {dimension!r} must be finite")
    return numeric_value
