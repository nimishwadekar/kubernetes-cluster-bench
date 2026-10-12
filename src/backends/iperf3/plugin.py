from __future__ import annotations

from ..plugin import BackendPlugin
from .analyzer import Iperf3Analyzer
from .runner import Iperf3Runner


def create_plugin() -> BackendPlugin:
    """Create the iperf3 runner/analyzer plugin descriptor."""

    return BackendPlugin(
        name="iperf3",
        runner_factory=Iperf3Runner,
        analyzer_factory=Iperf3Analyzer,
        description="TCP network throughput benchmark using iperf3",
        profile_options=frozenset({"duration_seconds", "warmup_seconds"}),
        profile_dimensions=frozenset({"threads"}),
        graph_dimensions=frozenset({"threads"}),
    )
