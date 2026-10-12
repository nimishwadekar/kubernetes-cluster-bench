from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from src.backends.nixl.runner import NixlRunner, _minimum_total_buffer_size
from src.benchmark.contracts import BackendResources, BenchmarkConfig


class FakeProcess:
    def __init__(self, status: int | None = None) -> None:
        self.status = status

    def poll(self):
        return self.status

    def wait(self, timeout=None):
        self.status = 0
        return 0


class NixlRunnerTests(unittest.TestCase):
    def config(self) -> BenchmarkConfig:
        return BenchmarkConfig(
            backend="nixl", test_id="test-1", initiator_node="worker-a",
            target_node="worker-b", namespace="bench", image="example/nixl:latest",
            log_directory=Path("logs"), runs=3, parameters={
                "threads": 2, "warmup_runs": 10,
                "block_size_start": 4 * 1024 * 1024,
                "block_size_max": 8 * 1024 * 1024,
                "batch_size_start": 1, "batch_size_max": 8,
            },
        )

    def test_validation_rejects_missing_or_invalid_warmup(self) -> None:
        runner = NixlRunner()
        with self.assertRaises(ValueError):
            runner.validate(replace(self.config(), parameters={}))
        with self.assertRaises(ValueError):
            runner.validate(replace(self.config(), parameters={"warmup_runs": -1}))

    def test_buffer_calculation_and_execute(self) -> None:
        self.assertEqual(_minimum_total_buffer_size(3, 8, 1), 24)
        runner = NixlRunner(kubectl=Mock())
        def start_exec(namespace, pod, command, path):
            path.write_text(
                "Using address 10.0.0.1 port 12345\n" if pod == "server" else "ready\n",
                encoding="utf-8",
            )
            return FakeProcess()
        runner.kubectl.start_exec.side_effect = start_exec
        resources = BackendResources(names={
            "initiator_pod": "server", "target_pod": "client", "initiator_ip": "10.0.0.1",
        })
        with tempfile.TemporaryDirectory() as directory:
            output = runner.execute(self.config(), resources, Path(directory) / "raw" / "t1")
            self.assertTrue(output.is_dir())
            runner.cleanup(self.config(), BackendResources(manifest_path=Path("manifest.yaml")))
        runner.kubectl.delete_manifest.assert_called_once()


if __name__ == "__main__":
    unittest.main()
