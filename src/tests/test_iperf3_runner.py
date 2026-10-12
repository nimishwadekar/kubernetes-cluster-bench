from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.backends.iperf3.runner import Iperf3Runner
from src.benchmark.contracts import BackendResources, BenchmarkConfig


class FakeProcess:
    def __init__(self, status: int = 0) -> None:
        self.status = status

    def poll(self):
        return self.status

    def wait(self, timeout=None):
        return self.status


class Iperf3RunnerTests(unittest.TestCase):
    def config(self) -> BenchmarkConfig:
        return BenchmarkConfig(
            backend="iperf3", test_id="test-1", initiator_node="worker-a",
            target_node="worker-b", namespace="bench", image="example/image:latest",
            log_directory=Path("logs"), parameters={
                "threads": 2, "duration_seconds": 5, "warmup_seconds": 1,
            },
        )

    def resources(self) -> BackendResources:
        return BackendResources(names={
            "server_service": "server", "server_pod": "server", "client_pod": "client",
        })

    def test_validation_rejects_invalid_values(self) -> None:
        runner = Iperf3Runner()
        for key, value in (("threads", 0), ("duration_seconds", 0), ("warmup_seconds", -1)):
            config = self.config()
            config = BenchmarkConfig(**{**config.__dict__, "parameters": {**config.parameters, key: value}})
            with self.assertRaises(ValueError):
                runner.validate(config)

    def test_execute_and_cleanup_with_mocked_processes(self) -> None:
        runner = Iperf3Runner(kubectl=Mock())
        def start_exec(namespace, pod, command, path):
            path.write_text("{}", encoding="utf-8")
            return FakeProcess()
        runner.kubectl.start_exec.side_effect = start_exec
        with tempfile.TemporaryDirectory() as directory:
            with patch("src.backends.iperf3.runner.wait_for_tcp_listener"):
                output = runner.execute(self.config(), self.resources(), Path(directory) / "raw" / "t1")
            self.assertTrue(output.is_dir())
            runner.cleanup(self.config(), BackendResources(manifest_path=Path("manifest.yaml")))
        runner.kubectl.delete_manifest.assert_called_once()


if __name__ == "__main__":
    unittest.main()
