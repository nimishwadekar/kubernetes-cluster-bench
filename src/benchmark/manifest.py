from __future__ import annotations

from collections.abc import Iterable
import re

import yaml


# kubectl parses manifests as YAML 1.1, where a bare y/n is a boolean. PyYAML
# quotes yes/no/on/off but not these, so they must be quoted explicitly.
_YAML11_SINGLE_LETTER_BOOLEAN = re.compile(r"[yYnN]")


class _ManifestDumper(yaml.SafeDumper):
    pass


def _represent_string(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    if _YAML11_SINGLE_LETTER_BOOLEAN.fullmatch(value):
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_ManifestDumper.add_representer(str, _represent_string)


def dump_manifest(documents: Iterable[object]) -> str:
    """Serialize Kubernetes documents so kubectl reads every string as a string."""

    return yaml.dump_all(
        documents, Dumper=_ManifestDumper, sort_keys=False, default_flow_style=False
    )
