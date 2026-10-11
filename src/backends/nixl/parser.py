from __future__ import annotations

import json
import math
from pathlib import Path
import re
from typing import Mapping, cast

from ...common.types import JsonValue
from ...graphing.contracts import Observation


JsonObject = Mapping[str, object]
_ROW_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+([0-9]+(?:\.[0-9]+)?)\s+"
)
_THREADS_RE = re.compile(r"Num threads \([^)]*\)\s*:\s*(\d+)")


class NixlParser:
    """Parse NIXLBench initiator tables into throughput observations."""

    def discover(self, raw_directory: Path) -> tuple[Path, ...]:
        directories = tuple(
            sorted(
                path
                for path in raw_directory.glob("t*")
                if path.is_dir() and any(path.glob("*-command.json"))
            )
        )
        if not directories:
            raise ValueError(f"no NIXL command metadata found in {raw_directory}")
        return directories

    def parse(self, run_directory: Path) -> tuple[Observation, ...]:
        command_files = sorted(run_directory.glob("*-command.json"))
        if not command_files:
            raise ValueError(f"no NIXL command metadata found in {run_directory}")
        observations: list[Observation] = []
        for command_path in command_files:
            metadata = _load_object(command_path)
            initiator_path = run_directory / _required_string(metadata, "initiator_file")
            target_path = run_directory / _required_string(metadata, "target_file")
            if not initiator_path.is_file():
                raise ValueError(f"NIXL initiator log not found: {initiator_path}")
            if not target_path.is_file():
                raise ValueError(f"NIXL target log not found: {target_path}")
            if metadata.get("initiator_status", 0) != 0 or metadata.get("target_status", 0) != 0:
                raise ValueError(f"NIXL run failed according to {command_path}")

            text = initiator_path.read_text(encoding="utf-8", errors="replace")
            threads = _effective_threads(text, metadata)
            run_number = _positive_int(metadata.get("run_number"), 1)
            test_id = _required_string(metadata, "test_id")
            run_id = f"{test_id}-t{threads}-run{run_number}"
            for row_index, line in enumerate(text.splitlines()):
                match = _ROW_RE.match(line)
                if match is None:
                    continue
                block_size, batch_size, gb_per_second = match.groups()
                throughput = float(gb_per_second) * 1e9
                if not math.isfinite(throughput):
                    raise ValueError(f"non-finite NIXL throughput in {initiator_path}")
                observations.append(
                    Observation(
                        metric="throughput",
                        value=throughput,
                        unit="bit/s",
                        dimensions={
                            "threads": threads,
                            "block_size_bytes": int(block_size),
                            "batch_size": int(batch_size),
                        },
                        sample_kind="table_row",
                        run_id=run_id,
                        metadata={
                            "row_index": row_index,
                            "source_unit": "GB/s",
                            "initiator_file": initiator_path.name,
                            "target_file": target_path.name,
                        },
                    )
                )
        if not observations:
            raise ValueError(f"no NIXL measurement rows found in {run_directory}")
        return tuple(observations)


def _load_object(path: Path) -> JsonObject:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read NIXL command metadata {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return cast(JsonObject, value)


def _required_string(root: JsonObject, field: str) -> str:
    value = root.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _effective_threads(text: str, metadata: JsonObject) -> int:
    match = _THREADS_RE.search(text)
    if match is not None:
        return int(match.group(1))
    parameters = metadata.get("parameters")
    if isinstance(parameters, dict):
        value = parameters.get("num_threads", parameters.get("threads", 1))
        return _positive_int(value, 1)
    return 1


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default
