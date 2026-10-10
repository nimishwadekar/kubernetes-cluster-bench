from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from ..common.types import JsonValue


@dataclass(frozen=True)
class RunOutcome:
    """Raw-artifact location and status for one attempted benchmark run."""

    thread_count: int
    run_number: int
    raw_directory: Path
    error: str | None = None

    @property
    def successful(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class BenchmarkExecution:
    """Filesystem result of one benchmark invocation."""

    backend: str
    test_id: str
    test_directory: Path
    manifest_path: Path
    outcomes: tuple[RunOutcome, ...]

    @property
    def raw_directory(self) -> Path:
        return self.test_directory / "raw"

    @property
    def successful(self) -> bool:
        return all(outcome.successful for outcome in self.outcomes)


def create_test_directory(
    log_directory: Path,
    backend: str,
    test_id: str,
) -> tuple[Path, Path]:
    """Create and return a test directory and its raw-artifact directory."""

    test_directory = log_directory / backend / test_id
    raw_directory = test_directory / "raw"
    raw_directory.mkdir(parents=True, exist_ok=True)
    return test_directory, raw_directory


def create_run_directory(
    raw_directory: Path,
    thread_count: int,
    run_number: int,
) -> Path:
    """Create the isolated raw-artifact directory for one run."""

    run_directory = raw_directory / f"t{thread_count}"
    run_directory.mkdir(parents=True, exist_ok=True)
    return run_directory


def write_json(path: Path, value: Mapping[str, JsonValue]) -> None:
    """Write a JSON artifact atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)
