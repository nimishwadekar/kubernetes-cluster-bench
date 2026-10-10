from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from src.backends.plugin import BackendPlugin
from src.backends.registry import BackendRegistry
from src.benchmark.cli import parse_config
from src.benchmark.contracts import BackendResources, BenchmarkConfig, RunContext
from src.benchmark.cli import main


class CliRunner:
    def validate(self, config: BenchmarkConfig) -> None:
        return None

    def render_manifest(self, config: BenchmarkConfig, destination: Path) -> None:
        destination.write_text("kind: Pod\n", encoding="utf-8")

    def deploy(self, config: BenchmarkConfig, manifest: Path) -> BackendResources:
        return BackendResources(manifest_path=manifest)

    def execute(
        self,
        config: BenchmarkConfig,
        resources: BackendResources,
        output_directory: Path,
        context: RunContext | None = None,
    ) -> Path:
        output_directory.joinpath("result.txt").write_text("ok\n", encoding="utf-8")
        return output_directory

    def cleanup(
        self,
        config: BenchmarkConfig,
        resources: BackendResources | None,
    ) -> None:
        return None


class BenchmarkCliTests(unittest.TestCase):
    def test_parse_config_keeps_common_and_backend_parameters(self) -> None:
        config = parse_config(
            [
                "--backend",
                "fake",
                "--client",
                "worker-a",
                "--server",
                "worker-b",
                "--test-id",
                "test-1",
                "--name",
                "friendly-name",
                "--threads",
                "1:4:2",
                "--run",
                "3",
                "--parameter",
                "runtime=ASIO",
                "--parameter",
                "num_iter=1000",
            ]
        )
        self.assertEqual(config.parameters["threads"], 1)
        self.assertEqual(config.parameters["threads_spec"], "1:4:2")
        self.assertEqual(config.parameters["runtime"], "ASIO")
        self.assertEqual(config.parameters["num_iter"], 1000)
        self.assertEqual(config.runs, 3)
        self.assertEqual(config.name, "friendly-name")

    def test_default_name_uses_test_id(self) -> None:
        config = parse_config(
            [
                "--backend", "iperf3",
                "--client", "worker-a", "--server", "worker-b", "--test-id", "test-1"
            ]
        )
        self.assertEqual(config.output_name, "test-1")

    def test_parse_iperf3_duration_options(self) -> None:
        config = parse_config(
            [
                "--backend", "iperf3",
                "--client", "worker-a",
                "--server", "worker-b",
                "--duration", "5",
                "--warmup", "2",
            ]
        )
        self.assertEqual(config.parameters["duration_seconds"], 5)
        self.assertEqual(config.parameters["warmup_seconds"], 2)

    def test_duration_and_transfer_size_are_mutually_exclusive(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                [
                    "--backend", "iperf3",
                    "--client", "worker-a",
                    "--server", "worker-b",
                    "--duration", "5",
                    "--transfer-size", "1M",
                ]
            )

    def test_main_dispatches_through_registry(self) -> None:
        registry = BackendRegistry(
            (BackendPlugin(name="fake", runner_factory=CliRunner),)
        )
        with tempfile.TemporaryDirectory() as directory:
            status = main(
                [
                    "--backend",
                    "fake",
                    "--client",
                    "worker-a",
                    "--server",
                    "worker-b",
                    "--test-id",
                    "test-1",
                    "--dir",
                    directory,
                ],
                registry,
            )
        self.assertEqual(status, 0)

    def test_equal_nodes_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_config(
                [
                    "--backend", "iperf3",
                    "--client",
                    "worker-a",
                    "--server",
                    "worker-a",
                ]
            )


if __name__ == "__main__":
    unittest.main()
