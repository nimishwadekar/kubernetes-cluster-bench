from __future__ import annotations

from pathlib import Path

from ..backends.plugin import BackendCapabilityError
from ..backends.registry import BackendRegistry
from .contracts import Observation


def load_observations(
    backend_name: str,
    directory: Path,
    registry: BackendRegistry,
) -> tuple[Observation, ...]:
    """Load backend observations from raw artifacts below a test directory."""

    plugin = registry.get(backend_name)
    analyzer = plugin.create_analyzer()
    raw_directory = directory / "raw" if directory.name != "raw" else directory
    if not raw_directory.is_dir():
        raise ValueError(f"raw directory not found: {raw_directory}")

    observations: list[Observation] = []
    for run_directory in analyzer.discover(raw_directory):
        observations.extend(analyzer.parse(run_directory))
    if not observations:
        raise ValueError(f"no observations found in {raw_directory}")
    return tuple(observations)


def discover_test_directories(directory: Path) -> tuple[Path, ...]:
    """Discover test directories without relying on their directory names."""

    if (directory / "raw").is_dir():
        return (directory,)
    candidates = {
        path.parent
        for path in directory.rglob("raw")
        if path.is_dir() and (path.parent / "manifest.yaml").is_file()
    }
    if not candidates:
        raise ValueError(f"no benchmark test directories found under {directory}")
    return tuple(sorted(candidates))
