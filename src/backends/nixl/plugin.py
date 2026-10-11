from __future__ import annotations

from ..plugin import BackendPlugin
from .analyzer import NixlAnalyzer
from .runner import NixlRunner


def create_plugin() -> BackendPlugin:
    """Create the NIXL runner/analyzer plugin descriptor."""

    return BackendPlugin(
        name="nixl",
        runner_factory=NixlRunner,
        analyzer_factory=NixlAnalyzer,
        description="GPU buffer-transfer benchmark using NIXLBench",
        default_image="quay.io/nwadekar/nixl-benchmark:og-nixl1.4.1-ucx1.22.0",
    )
