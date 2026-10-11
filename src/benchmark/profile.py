from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


def load_profile(path: Path) -> dict[str, object]:
    """Load and validate a benchmark profile YAML mapping."""

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"profile file not found: {path}") from error
    except OSError as error:
        raise ValueError(f"unable to read profile {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ValueError(f"invalid profile YAML in {path}: {error}") from error

    if not isinstance(raw, Mapping):
        raise ValueError("profile must contain a YAML mapping at the top level")
    profile: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError("profile keys must be strings")
        profile[key] = value
    return profile


def profile_section(
    profile: Mapping[str, object],
    name: str,
) -> dict[str, object]:
    """Return a named profile section as a string-keyed mapping."""

    value = profile.get(name, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"profile section {name!r} must be a mapping")
    section: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"profile section {name!r} keys must be strings")
        section[key] = item
    return section
