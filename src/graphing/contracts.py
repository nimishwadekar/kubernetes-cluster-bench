from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Mapping, Protocol

from ..common.types import DimensionValue, JsonValue


class MissingDimensionError(ValueError):
    """Raised when a requested graph dimension is absent from observations."""

    def __init__(self, dimension: str, observation_indexes: tuple[int, ...]) -> None:
        indexes = ", ".join(str(index) for index in observation_indexes)
        super().__init__(
            f"requested dimension {dimension!r} is missing from observation(s): {indexes}"
        )
        self.dimension = dimension
        self.observation_indexes = observation_indexes


@dataclass(frozen=True)
class Observation:
    """One backend-independent measurement before report-level aggregation."""

    metric: str
    value: float
    unit: str
    dimensions: Mapping[str, DimensionValue]
    sample_kind: str
    run_id: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.metric:
            raise ValueError("observation metric must not be empty")
        if not self.unit:
            raise ValueError("observation unit must not be empty")
        if not self.sample_kind:
            raise ValueError("observation sample_kind must not be empty")
        if not self.run_id:
            raise ValueError("observation run_id must not be empty")
        if not math.isfinite(self.value):
            raise ValueError("observation value must be finite")
        for name, value in self.dimensions.items():
            if not name:
                raise ValueError("observation dimension names must not be empty")
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError(
                    f"observation dimension {name!r} must be a string or number"
                )
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(
                    f"observation dimension {name!r} must be finite"
                )


@dataclass(frozen=True)
class AnalysisRequest:
    """User-selected graph dimensions and filters."""

    metric: str
    x_dimension: str
    filters: Mapping[str, DimensionValue] = field(default_factory=dict)
    series_dimensions: tuple[str, ...] = ()
    percentile: float | None = None

    def __post_init__(self) -> None:
        if not self.metric:
            raise ValueError("analysis metric must not be empty")
        if not self.x_dimension:
            raise ValueError("analysis x_dimension must not be empty")
        if self.percentile is not None and not 0 <= self.percentile <= 100:
            raise ValueError("analysis percentile must be between 0 and 100")


@dataclass(frozen=True)
class GraphPoint:
    """Backend-independent point consumed by the shared graph renderer."""

    x_value: float
    y_value: float
    x_label: str
    series_key: tuple[str, ...] = ()
    y_error: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if not self.x_label:
            raise ValueError("graph point x_label must not be empty")
        if not math.isfinite(self.x_value):
            raise ValueError("graph point x_value must be finite")
        if not math.isfinite(self.y_value):
            raise ValueError("graph point y_value must be finite")
        if self.y_error is not None and not all(
            math.isfinite(error) and error >= 0 for error in self.y_error
        ):
            raise ValueError("graph point y_error values must be finite and non-negative")


class ResultAnalyzer(Protocol):
    """Capability for reading raw files and producing graph-ready points."""

    def discover(self, raw_directory: Path) -> tuple[Path, ...]:
        """Find complete backend runs in a raw-artifact directory."""
        ...

    def parse(self, run_directory: Path) -> tuple[Observation, ...]:
        """Parse one backend run without statistical aggregation."""
        ...

    def aggregate(
        self,
        observations: tuple[Observation, ...],
        request: AnalysisRequest,
    ) -> tuple[GraphPoint, ...]:
        """Apply backend-appropriate statistics and return graph points."""
        ...
