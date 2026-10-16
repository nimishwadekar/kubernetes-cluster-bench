from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..common.types import JsonValue

if TYPE_CHECKING:
    from .contracts import BenchmarkConfig


def parse_thread_spec(value: str) -> tuple[int, ...]:
    parts = value.split(":")
    if len(parts) not in (1, 2, 3) or any(not part.isdigit() for part in parts):
        raise ValueError("threads must use start[:end[:step]] with positive values")
    numbers = [int(part) for part in parts]
    start = numbers[0]
    end = start if len(numbers) == 1 else numbers[1]
    step = 1 if len(numbers) < 3 else numbers[2]
    if start < 1 or end < start or step < 1:
        raise ValueError("threads must use positive values with start <= end")
    return tuple(range(start, end + 1, step))


def format_eta(seconds: float) -> str:
    remaining = max(0, int(round(seconds)))
    minutes, seconds = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def resource_overrides(config: BenchmarkConfig) -> dict[str, JsonValue]:
    return {
        "requests": dict(config.resources.requests),
        "limits": dict(config.resources.limits),
    }
