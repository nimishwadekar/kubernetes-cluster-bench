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
        profile_options=frozenset({"warmup_runs", "run_timeout_seconds"}),
        profile_dimensions=frozenset({"threads", "block_size", "batch_size"}),
        graph_dimensions=frozenset({"threads", "block_size", "batch_size"}),
        framework_repeats=False,
    )
