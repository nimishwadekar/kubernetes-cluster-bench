from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from src.backends.nixl.analyzer import NixlAnalyzer
from src.backends.nixl.parser import NixlParser
from src.backends.nixl.plugin import create_plugin
from src.backends.nixl.runner import NixlRunner
from src.backends.registry import default_registry
from src.benchmark.contracts import BackendResources, BenchmarkConfig
from src.graphing.contracts import AnalysisRequest


class NixlPluginTests(unittest.TestCase):
    def make_config(self) -> BenchmarkConfig:
        return BenchmarkConfig(
            backend="nixl",
            test_id="nixl-test",
            initiator_node="worker-a",
            target_node="worker-b",
            namespace="bench",
            image="example/nixl:latest",
            log_directory=Path("logs"),
            parameters={"warmup_runs": 10},
        )

    def make_fixture(self, directory: Path, threads: int = 2) -> Path:
        raw = directory / "raw"
        run = raw / f"t{threads}"
        run.mkdir(parents=True)
        source = Path(__file__).parents[2] / "nixl" / f"server-t{threads}.log"
        shutil.copyfile(source, run / "nixl-test-t2-run1-initiator.log")
        shutil.copyfile(
            Path(__file__).parents[2] / "nixl" / "client.log",
            run / "nixl-test-t2-run1-target.log",
        )
        command = {
            "backend": "nixl",
            "test_id": "nixl-test",
            "run_number": 1,
            "parameters": {"num_threads": threads},
            "initiator_file": "nixl-test-t2-run1-initiator.log",
            "target_file": "nixl-test-t2-run1-target.log",
            "initiator_status": 0,
            "target_status": 0,
        }
        (run / "nixl-test-t2-run1-command.json").write_text(
            json.dumps(command), encoding="utf-8"
        )
        return raw

    def test_plugin_is_registered(self) -> None:
        plugin = create_plugin()
        self.assertIsInstance(plugin.create_runner(), NixlRunner)
        self.assertIsInstance(plugin.create_analyzer(), NixlAnalyzer)
        self.assertIn("nixl", default_registry().names())
        self.assertEqual(
            plugin.graph_dimensions,
            frozenset({"threads", "block_size", "batch_size"}),
        )

    def test_parser_reads_twenty_table_rows(self) -> None:
        parser = NixlParser()
        with tempfile.TemporaryDirectory() as directory:
            raw = self.make_fixture(Path(directory))
            observations = parser.parse(parser.discover(raw)[0])
        self.assertEqual(len(observations), 20)
        self.assertEqual(observations[0].dimensions["threads"], 2)
        self.assertEqual(observations[0].dimensions["block_size_bytes"], 4194304)
        self.assertEqual(observations[0].dimensions["batch_size"], 1)
        self.assertAlmostEqual(observations[0].value, 1.575178e9 * 8)

    def test_analyzer_requires_filter_or_series_for_varying_dimensions(self) -> None:
        parser = NixlParser()
        analyzer = NixlAnalyzer(parser)
        with tempfile.TemporaryDirectory() as directory:
            raw = self.make_fixture(Path(directory))
            observations = analyzer.parse(analyzer.discover(raw)[0])
            with self.assertRaises(ValueError):
                analyzer.aggregate(
                    observations,
                    AnalysisRequest(metric="throughput", x_dimension="block_size_bytes"),
                )

    def test_analyzer_filters_batch_size(self) -> None:
        parser = NixlParser()
        analyzer = NixlAnalyzer(parser)
        with tempfile.TemporaryDirectory() as directory:
            raw = self.make_fixture(Path(directory))
            observations = analyzer.parse(analyzer.discover(raw)[0])
            points = analyzer.aggregate(
                observations,
                AnalysisRequest(
                    metric="throughput",
                    x_dimension="block_size_bytes",
                    filters={"batch_size": 8},
                ),
            )
        self.assertEqual(len(points), 5)
        self.assertEqual(points[0].x_value, 4194304)
        self.assertEqual(points[0].y_value, 2.294085 * 8)

    def test_runner_passes_calculated_buffer_and_runtime_arguments(self) -> None:
        runner = NixlRunner()
        args = runner._nixl_args(2, 50, 10, 1073741824, 4194304, 67108864, 1, 8, "10.0.0.1")
        self.assertEqual(
            args,
            [
                "--runtime_type", "ASIO",
                "--asio_address", "10.0.0.1",
                "--asio_port", "12345",
                "--backend", "UCX",
                "--initiator_seg_type", "DRAM",
                "--target_seg_type", "DRAM",
                "--total_buffer_size", "1073741824",
                "--start_block_size", "4194304",
                "--max_block_size", "67108864",
                "--start_batch_size", "1",
                "--max_batch_size", "8",
                "--num_threads", "2",
                "--num_iter", "50",
                "--warmup_iter", "10",
            ],
        )
    def test_runner_renders_manifest_with_role_mapping(self) -> None:
        runner = NixlRunner()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "manifest.yaml"
            runner.render_manifest(self.make_config(), destination)
            text = destination.read_text(encoding="utf-8")
        self.assertIn("nodeName: worker-a", text)
        self.assertIn("nodeName: worker-b", text)
        self.assertNotIn("__TEST_ID__", text)


if __name__ == "__main__":
    unittest.main()
