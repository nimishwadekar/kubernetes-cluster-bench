from __future__ import annotations

from dataclasses import replace
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

    def test_resources_override_cpu_and_memory(self) -> None:
        import yaml
        from src.benchmark.resources import PodResources

        config = replace(
            self.config(),
            resources=PodResources({"cpu": "4", "memory": "4Gi"}, {"cpu": "4", "memory": "4Gi"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "manifest.yaml"
            Iperf3Runner().render_manifest(config, destination)
            docs = [d for d in yaml.safe_load_all(destination.read_text(encoding="utf-8")) if d["kind"] == "Pod"]
        self.assertEqual(len(docs), 2)
        for doc in docs:
            resources = doc["spec"]["containers"][0]["resources"]
            self.assertEqual(resources["requests"], {"cpu": "4", "memory": "4Gi"})
            self.assertEqual(resources["limits"], {"cpu": "4", "memory": "4Gi"})

    def test_invalid_resources_fail_before_deploy(self) -> None:
        from src.benchmark.resources import PodResources

        config = replace(self.config(), resources=PodResources({"cpu": "16"}))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "exceeds limit"):
                Iperf3Runner().render_manifest(config, Path(directory) / "m.yaml")


if __name__ == "__main__":
    unittest.main()
