from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from src.backends.nixl.runner import NixlRunner, _minimum_total_buffer_size
from src.benchmark.contracts import BackendResources, BenchmarkConfig
from src.benchmark.network import NetworkAttachment, SecondaryNetwork


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

    def test_validation_rejects_invalid_run_timeout(self) -> None:
        runner = NixlRunner()
        config = self.config()
        for value in (0, -5, True, "10"):
            with self.assertRaises(ValueError):
                runner.validate(
                    replace(config, parameters={**config.parameters, "run_timeout_seconds": value})
                )

    def test_execute_times_out_and_terminates_processes(self) -> None:
        class HangingProcess(FakeProcess):
            terminated = False

            def wait(self, timeout=None):
                if timeout is not None and not self.terminated:
                    raise subprocess.TimeoutExpired("nixlbench", timeout)
                return super().wait(timeout)

            def send_signal(self, _signal):
                self.terminated = True

        processes = {}
        def start_exec(namespace, pod, command, path):
            path.write_text("Using address 10.0.0.1 port 12345\n", encoding="utf-8")
            processes[pod] = HangingProcess()
            return processes[pod]

        runner = NixlRunner(kubectl=Mock())
        runner.kubectl.start_exec.side_effect = start_exec
        resources = BackendResources(names={
            "initiator_pod": "server", "target_pod": "client", "initiator_ip": "10.0.0.1",
        })
        config = self.config()
        config = replace(config, parameters={**config.parameters, "run_timeout_seconds": 1})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                runner.execute(config, resources, Path(directory) / "raw" / "t1")
        self.assertTrue(all(process.terminated for process in processes.values()))

    def test_render_manifest_applies_image_pull_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "template.yaml"
            template.write_text(
                "__TEST_ID__ __CLIENT_NODE__ __SERVER_NODE__ __IMAGE__ "
                "policy=__IMAGE_PULL_POLICY__",
                encoding="utf-8",
            )
            destination = Path(directory) / "out.yaml"
            NixlRunner(manifest_template=template).render_manifest(
                replace(self.config(), image_pull_policy="Never"), destination
            )
            self.assertIn("policy=Never", destination.read_text(encoding="utf-8"))

    def test_gpu_false_removes_gpu_request_and_cuda_transport(self) -> None:
        runner = NixlRunner()
        with tempfile.TemporaryDirectory() as directory:
            gpu = Path(directory) / "gpu.yaml"
            cpu = Path(directory) / "cpu.yaml"
            runner.render_manifest(self.config(), gpu)
            runner.render_manifest(
                replace(self.config(), parameters={**self.config().parameters, "gpu": False}),
                cpu,
            )
            gpu_text = gpu.read_text(encoding="utf-8")
            cpu_text = cpu.read_text(encoding="utf-8")
        self.assertEqual(gpu_text.count("nvidia.com/gpu"), 4)
        self.assertIn("tcp,self,cuda_copy", gpu_text)
        self.assertNotIn("nvidia.com/gpu", cpu_text)
        self.assertNotIn("cuda_copy", cpu_text)
        self.assertEqual(cpu_text.count("value: tcp,self"), 2)
        self.assertIn("memory: 32Gi", cpu_text)  # other limits are kept
        # YAML 1.1 readers (kubectl) turn a bare n into false.
        self.assertIn('value: "n"', cpu_text)

    def test_validation_rejects_non_boolean_gpu(self) -> None:
        config = self.config()
        with self.assertRaises(ValueError):
            NixlRunner().validate(
                replace(config, parameters={**config.parameters, "gpu": "no"})
            )

    def network(self) -> SecondaryNetwork:
        return SecondaryNetwork(
            initiator=NetworkAttachment("cx7-a", "net1"),
            target=NetworkAttachment("cx7-b", "net2"),
        )

    def test_render_manifest_attaches_network_and_pins_ucx_devices(self) -> None:
        import yaml

        config = replace(self.config(), network=self.network())
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "manifest.yaml"
            NixlRunner().render_manifest(config, destination)
            docs = list(yaml.safe_load_all(destination.read_text(encoding="utf-8")))
        pods = {doc["metadata"]["name"]: doc for doc in docs}
        initiator = pods["nixlbench-server-test-1"]
        target = pods["nixlbench-client-test-1"]
        self.assertIn("cx7-a", initiator["metadata"]["annotations"]["k8s.v1.cni.cncf.io/networks"])
        self.assertIn("cx7-b", target["metadata"]["annotations"]["k8s.v1.cni.cncf.io/networks"])

        def env(pod):
            return {e["name"]: e["value"] for e in pod["spec"]["containers"][0]["env"]}

        self.assertEqual(env(initiator)["UCX_NET_DEVICES"], "net1")
        self.assertEqual(env(target)["UCX_NET_DEVICES"], "net2")
        self.assertEqual(env(initiator)["UCX_TLS"], "tcp,self,cuda_copy")  # untouched

    def test_no_network_leaves_ucx_devices_unset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "manifest.yaml"
            NixlRunner().render_manifest(self.config(), destination)
            text = destination.read_text(encoding="utf-8")
        self.assertNotIn("UCX_NET_DEVICES", text)
        self.assertNotIn("k8s.v1.cni.cncf.io", text)

    def test_deploy_uses_secondary_address_for_the_rendezvous(self) -> None:
        kubectl = Mock()
        kubectl.resource_exists.side_effect = lambda resource, namespace: (
            resource.startswith("network-attachment-definitions")
        )
        def output(args):
            if args[2] == "nixlbench-server-test-1":
                return '[{"interface": "net1", "ips": ["10.77.0.35"]}]'
            return '[{"interface": "net2", "ips": ["10.77.0.36"]}]'
        kubectl.output.side_effect = output
        resources = NixlRunner(kubectl=kubectl).deploy(
            replace(self.config(), network=self.network()), Path("manifest.yaml")
        )
        self.assertEqual(resources.names["initiator_ip"], "10.77.0.35")
        self.assertEqual(resources.names["initiator_address"], "10.77.0.35")
        self.assertEqual(resources.names["target_address"], "10.77.0.36")
        self.assertFalse(any(
            "podIP" in str(call) for call in kubectl.output.call_args_list
        ))

    def test_deploy_rejects_missing_attachment_before_creating_pods(self) -> None:
        kubectl = Mock()
        kubectl.resource_exists.return_value = False
        with self.assertRaisesRegex(ValueError, "not found"):
            NixlRunner(kubectl=kubectl).deploy(
                replace(self.config(), network=self.network()), Path("manifest.yaml")
            )
        kubectl.apply_manifest.assert_not_called()

    def test_resources_override_cpu_and_memory_but_keep_gpu(self) -> None:
        import yaml
        from src.benchmark.resources import PodResources

        config = replace(
            self.config(),
            resources=PodResources({"cpu": "4"}, {"cpu": "4", "memory": "16Gi"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "manifest.yaml"
            NixlRunner().render_manifest(config, destination)
            docs = list(yaml.safe_load_all(destination.read_text(encoding="utf-8")))
        for doc in docs:
            resources = doc["spec"]["containers"][0]["resources"]
            self.assertEqual(resources["requests"]["cpu"], "4")
            self.assertEqual(resources["requests"]["memory"], "8Gi")  # template default kept
            self.assertEqual(resources["limits"], {"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"})

    def test_render_manifest_rejects_leftover_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "template.yaml"
            template.write_text(
                "__TEST_ID__ __CLIENT_NODE__ __SERVER_NODE__ __IMAGE__ __IMAGE_PULL_POLICY__", encoding="utf-8"
            )
            runner = NixlRunner(manifest_template=template)
            config = replace(self.config(), image="registry/__TEST_ID__:tag")
            with self.assertRaisesRegex(ValueError, "still contains"):
                runner.render_manifest(config, Path(directory) / "out.yaml")


if __name__ == "__main__":
    unittest.main()
