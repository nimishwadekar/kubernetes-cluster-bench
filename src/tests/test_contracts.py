from __future__ import annotations

from pathlib import Path
import unittest

from src.backends.plugin import BackendCapabilityError, BackendPlugin
from src.benchmark.contracts import (
    BackendResources,
    BenchmarkConfig,
    BenchmarkRunner,
)
from src.graphing.contracts import (
    AnalysisRequest,
    GraphPoint,
    Observation,
    ResultAnalyzer,
)


class DummyRunner:
    pass


class DummyAnalyzer:
    pass


class ContractTests(unittest.TestCase):
    def test_benchmark_contracts_own_execution_types(self) -> None:
        config = BenchmarkConfig(
            backend="iperf3",
            benchmark="network-throughput",
            test_id="test-1",
            initiator_node="worker-a",
            target_node="worker-b",
            namespace="bench",
            image="example/iperf3:latest",
            log_directory=Path("logs"),
        )
        resources = BackendResources(names={"server_pod": "server"})
        self.assertEqual(config.backend, "iperf3")
        self.assertEqual(resources.names["server_pod"], "server")

    def test_observation_preserves_backend_sample_shape(self) -> None:
        observation = Observation(
            metric="throughput",
            value=2.5e9,
            unit="bit/s",
            dimensions={
                "threads": 2,
                "block_size_bytes": 4 * 1024 * 1024,
            },
            sample_kind="table_row",
            run_id="run-1",
            metadata={"source": "nixl"},
        )
        self.assertEqual(observation.sample_kind, "table_row")
        self.assertEqual(observation.dimensions["threads"], 2)
        self.assertEqual(observation.metadata["source"], "nixl")

    def test_analysis_request_supports_filters_series_and_percentile(self) -> None:
        request = AnalysisRequest(
            metric="throughput",
            x_dimension="block_size_bytes",
            filters={"threads": 4},
            series_dimensions=("batch_size",),
            percentile=95,
        )
        self.assertEqual(request.filters["threads"], 4)
        self.assertEqual(request.series_dimensions, ("batch_size",))

    def test_plugin_can_create_both_capabilities(self) -> None:
        plugin = BackendPlugin(
            name="test",
            runner_factory=DummyRunner,  # type: ignore[arg-type]
            analyzer_factory=DummyAnalyzer,  # type: ignore[arg-type]
        )
        self.assertIsInstance(plugin.create_runner(), DummyRunner)
        self.assertIsInstance(plugin.create_analyzer(), DummyAnalyzer)

    def test_plugin_capability_errors_are_explicit(self) -> None:
        plugin = BackendPlugin(name="graph-only", analyzer_factory=DummyAnalyzer)  # type: ignore[arg-type]
        with self.assertRaises(BackendCapabilityError):
            plugin.create_runner()

    def test_graph_point_and_validation_rules(self) -> None:
        point = GraphPoint(
            x_value=4,
            y_value=2.5,
            x_label="4",
            series_key=("batch=8",),
            y_error=(0.1, 0.1),
        )
        self.assertEqual(point.series_key, ("batch=8",))
        with self.assertRaises(ValueError):
            Observation(
                metric="throughput",
                value=float("nan"),
                unit="bit/s",
                dimensions={},
                sample_kind="interval",
                run_id="run-1",
            )
        with self.assertRaises(ValueError):
            AnalysisRequest(metric="throughput", x_dimension="threads", percentile=101)


if __name__ == "__main__":
    unittest.main()
