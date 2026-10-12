from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.backends.iperf3.analyzer import Iperf3Analyzer
from src.backends.iperf3.parser import Iperf3Parser
from src.backends.iperf3.plugin import create_plugin
from src.backends.iperf3.runner import Iperf3Runner
from src.backends.registry import default_registry
from src.benchmark.contracts import BackendResources, BenchmarkConfig
from src.graphing.contracts import AnalysisRequest


class Iperf3PluginTests(unittest.TestCase):
    def make_config(self, **parameters: object) -> BenchmarkConfig:
        return BenchmarkConfig(
            backend="iperf3",
            test_id="test-1",
            initiator_node="worker-a",
            target_node="worker-b",
            namespace="bench",
            image="example/iperf3:latest",
            log_directory=Path("logs"),
            parameters=parameters,
        )

    def write_run(self, directory: Path, *, run_number: int, values: list[dict[str, object]]) -> Path:
        run_directory = directory / "t1"
        run_directory.mkdir(parents=True, exist_ok=True)
        command = {
            "backend": "iperf3",
            "test_id": "test-1",
            "run_number": run_number,
            "kube_context": "context",
            "initiator_node": "worker-a",
            "target_node": "worker-b",
            "namespace": "bench",
            "image": "example/iperf3:latest",
            "parameters": {"threads": 1, "duration_seconds": 5, "warmup_seconds": 2},
            "client_file": f"test-1-t1-run{run_number}-client.json",
            "server_file": "../test-1-server.json",
            "client_status": 0,
            "started_at": "2026-10-09T00:00:00Z",
            "finished_at": "2026-10-09T00:00:10Z",
        }
        (run_directory / f"test-1-t1-run{run_number}-command.json").write_text(
            json.dumps(command), encoding="utf-8"
        )
        client = {
            "start": {"test_start": {"num_streams": 1}},
            "server_output_json": {"intervals": [{"sum": value} for value in values]},
        }
        (run_directory / f"test-1-t1-run{run_number}-client.json").write_text(
            json.dumps(client), encoding="utf-8"
        )
        (directory / "test-1-server.json").write_text("server\n", encoding="utf-8")
        return run_directory

    def test_plugin_is_registered_with_both_capabilities(self) -> None:
        plugin = create_plugin()
        self.assertEqual(plugin.name, "iperf3")
        self.assertIsInstance(plugin.create_runner(), Iperf3Runner)
        self.assertIsInstance(plugin.create_analyzer(), Iperf3Analyzer)
        self.assertEqual(plugin.graph_dimensions, frozenset({"threads"}))
        self.assertIn("iperf3", default_registry().names())

    def test_runner_commands_match_legacy_iperf3_commands(self) -> None:
        runner = Iperf3Runner()
        config = self.make_config(threads=2, duration_seconds=10, warmup_seconds=5)
        resources = BackendResources(
            names={
                "server_service": "network-benchmark-server-test-1",
                "server_pod": "network-benchmark-server-test-1",
                "client_pod": "network-benchmark-client-test-1",
            }
        )
        self.assertEqual(
            runner.client_command(config, resources),
            [
                "kubectl", "exec", "-n", "bench",
                "network-benchmark-client-test-1", "--", "iperf3", "-c",
                "network-benchmark-server-test-1", "-J", "-P", "2",
                "--get-server-output", "-t", "15", "-O", "5",
            ],
        )
    def test_parser_preserves_intervals_and_marks_legacy_validity(self) -> None:
        parser = Iperf3Parser()
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            self.write_run(
                raw,
                run_number=1,
                values=[
                    {"start": 0, "end": 1, "seconds": 1, "bits_per_second": 100, "omitted": True},
                    {"start": 1, "end": 2, "seconds": 1, "bits_per_second": 1e9, "omitted": False},
                    {"start": 2, "end": 4, "seconds": 1, "bits_per_second": 2e9, "omitted": False},
                    {"start": 4, "end": 5, "seconds": 1, "bits_per_second": 3e9, "omitted": False},
                ],
            )
            observations = parser.parse(raw / "t1")
        self.assertEqual(len(observations), 4)
        self.assertEqual(
            [observation.metadata["valid"] for observation in observations],
            [False, True, False, True],
        )
        self.assertEqual(observations[1].dimensions["threads"], 1)

    def test_analyzer_matches_legacy_run_and_repeat_aggregation(self) -> None:
        parser = Iperf3Parser()
        analyzer = Iperf3Analyzer(parser)
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            for run_number, values in (
                (1, [{"start": 0, "end": 1, "seconds": 1, "bits_per_second": 1e9, "omitted": False},
                     {"start": 1, "end": 2, "seconds": 1, "bits_per_second": 3e9, "omitted": False}]),
                (2, [{"start": 0, "end": 1, "seconds": 1, "bits_per_second": 3e9, "omitted": False},
                     {"start": 1, "end": 2, "seconds": 1, "bits_per_second": 5e9, "omitted": False}]),
            ):
                self.write_run(raw, run_number=run_number, values=values)
            observations = tuple(
                observation
                for run_directory in analyzer.discover(raw)
                for observation in analyzer.parse(run_directory)
            )
            points = analyzer.aggregate(
                observations,
                AnalysisRequest(metric="throughput", x_dimension="threads"),
            )
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].y_value, 3.0)
        self.assertAlmostEqual(points[0].y_error[0], 1.414213562, delta=1e-9)

    def test_analyzer_applies_percentile_before_repeat_aggregation(self) -> None:
        parser = Iperf3Parser()
        analyzer = Iperf3Analyzer(parser)
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            self.write_run(
                raw,
                run_number=1,
                values=[
                    {"start": 0, "end": 1, "seconds": 1, "bits_per_second": 1e9, "omitted": False},
                    {"start": 1, "end": 2, "seconds": 1, "bits_per_second": 3e9, "omitted": False},
                ],
            )
            observations = analyzer.parse(raw / "t1")
            points = analyzer.aggregate(
                observations,
                AnalysisRequest(metric="throughput", x_dimension="threads", percentile=50),
            )
        self.assertEqual(points[0].y_value, 2.0)


if __name__ == "__main__":
    unittest.main()
