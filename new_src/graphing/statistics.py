from __future__ import annotations

import math
import statistics
from typing import Sequence


def mean(values: Sequence[float]) -> float:
    """Return the arithmetic mean of finite values."""

    _require_values(values)
    _require_finite(values)
    return statistics.fmean(values)


def standard_deviation(values: Sequence[float]) -> float:
    """Return sample standard deviation, or zero for one value."""

    _require_values(values)
    _require_finite(values)
    return statistics.stdev(values) if len(values) > 1 else 0.0


def percentile(values: Sequence[float], percentile_value: float) -> float:
    """Return a linearly interpolated percentile of finite values."""

    _require_values(values)
    _require_finite(values)
    if not 0 <= percentile_value <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_value / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def mean_and_standard_deviation(values: Sequence[float]) -> tuple[float, float]:
    """Return the mean and sample standard deviation for one value set."""

    return mean(values), standard_deviation(values)


def _require_values(values: Sequence[float]) -> None:
    if not values:
        raise ValueError("at least one value is required")


def _require_finite(values: Sequence[float]) -> None:
    if not all(math.isfinite(value) for value in values):
        raise ValueError("all values must be finite")
