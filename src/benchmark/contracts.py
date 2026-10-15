from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Callable, Mapping, Protocol

from ..common.console import Colors
from .resources import PodResources
from ..common.types import JsonValue


DEFAULT_MAX_CONSECUTIVE_FAILURES = 5
IMAGE_PULL_POLICIES = ("Always", "IfNotPresent", "Never")
DEFAULT_IMAGE_PULL_POLICY = "Always"


def _empty_json_mapping() -> dict[str, JsonValue]:
    return {}


def _empty_string_mapping() -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class BenchmarkConfig:
    """Backend-independent configuration supplied by the benchmark frontend."""

    backend: str
    test_id: str
    initiator_node: str
    target_node: str
    namespace: str
    image: str
    log_directory: Path
    name: str | None = None
    kube_context: str = "unknown"
    runs: int = 1
    timeout_seconds: int = 120
    keep_resources: bool = False
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES
    image_pull_policy: str = DEFAULT_IMAGE_PULL_POLICY
    resources: PodResources = field(default_factory=PodResources)
    parameters: Mapping[str, JsonValue] = field(default_factory=_empty_json_mapping)

    def __post_init__(self) -> None:
        for field_name in (
            "backend",
            "test_id",
            "initiator_node",
            "target_node",
            "namespace",
            "image",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must not be empty")
        if self.initiator_node == self.target_node:
            raise ValueError("initiator_node and target_node must be different")
        if self.name is not None and re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]*", self.name
        ) is None:
            raise ValueError(
                "name must contain only letters, numbers, dots, underscores, and hyphens"
            )
        if self.runs < 1:
            raise ValueError("runs must be positive")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        if self.image_pull_policy not in IMAGE_PULL_POLICIES:
            raise ValueError(
                "image_pull_policy must be one of: " + ", ".join(IMAGE_PULL_POLICIES)
            )
        if self.max_consecutive_failures < 0:
            raise ValueError("max_consecutive_failures must not be negative")

    @property
    def output_name(self) -> str:
        return self.name or self.test_id


@dataclass(frozen=True)
class RunContext:
    """Common execution progress context passed to a backend runner."""

    completed_runs: int
    total_runs: int
    benchmark_started_monotonic: float
    colors: Colors | None = None
    report: Callable[[str], None] | None = None


@dataclass(frozen=True)
class BackendResources:
    """Handles for resources owned by one backend execution."""

    names: Mapping[str, str] = field(default_factory=_empty_string_mapping)
    manifest_path: Path | None = None


class BenchmarkRunner(Protocol):
    """Capability for executing and collecting one backend benchmark."""

    def validate(self, config: BenchmarkConfig) -> None:
        """Validate backend-specific configuration."""
        ...

    def render_manifest(self, config: BenchmarkConfig, destination: Path) -> None:
        """Render the backend's deployment artifact."""
        ...

    def deploy(self, config: BenchmarkConfig, manifest: Path) -> BackendResources:
        """Create backend resources and return resource handles."""
        ...

    def execute(
        self,
        config: BenchmarkConfig,
        resources: BackendResources,
        output_directory: Path,
        context: RunContext | None = None,
    ) -> Path:
        """Execute one run and return its raw-artifact directory."""
        ...

    def cleanup(
        self,
        config: BenchmarkConfig,
        resources: BackendResources | None,
    ) -> None:
        """Stop processes and remove resources created by the runner."""
        ...
