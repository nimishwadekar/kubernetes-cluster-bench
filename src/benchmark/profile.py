from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any

import yaml


class ProfileLoader(yaml.SafeLoader):
    """YAML 1.2-like loader that preserves colon-separated ranges as strings."""


ProfileLoader.yaml_implicit_resolvers = {
    key: list(resolvers)
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_INT_PATTERN = re.compile(
    r"""^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+)$"""
)
for _key, _resolvers in ProfileLoader.yaml_implicit_resolvers.items():
    _resolvers[:] = [
        (tag, pattern)
        for tag, pattern in _resolvers
        if tag != "tag:yaml.org,2002:int"
    ]
    _resolvers.append(("tag:yaml.org,2002:int", _INT_PATTERN))


def load_profile(path: Path) -> dict[str, object]:
    """Load and validate a benchmark profile YAML mapping."""

    try:
        raw: Any = yaml.load(
            path.read_text(encoding="utf-8"),
            Loader=ProfileLoader,
        )
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
