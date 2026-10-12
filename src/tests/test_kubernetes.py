from __future__ import annotations

from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from src.benchmark.kubernetes import KubectlClient, KubectlError, wait_for_tcp_listener


class Process:
    def __init__(self) -> None:
        self.status = None

    def poll(self):
        return self.status

    def wait(self):
        return self.status or 0


class KubernetesTests(unittest.TestCase):
    def test_wrapper_success_and_error(self) -> None:
        client = KubectlClient(executable="kubectl")
        with patch("src.benchmark.kubernetes.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=["kubectl"], returncode=0, stdout="value\n", stderr=""
            )
            self.assertEqual(client.output(["get", "x"]), "value")
            self.assertTrue(client.resource_exists("pod/x", "bench"))
            client.apply_manifest("bench", Path("manifest.yaml"))
            client.wait_for("bench", "pod/x", "condition=Ready", 5)
            self.assertTrue(client.delete_manifest("bench", Path("manifest.yaml")))
            run.return_value = subprocess.CompletedProcess(
                args=["kubectl"], returncode=1, stdout="", stderr="nope"
            )
            with self.assertRaises(KubectlError):
                client.run(["get", "x"])

    def test_listener_wait_detects_socket(self) -> None:
        client = KubectlClient()
        with patch.object(KubectlClient, "run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=["kubectl"], returncode=0,
                stdout="LISTEN 0 128 0.0.0.0:5201 0.0.0.0:*", stderr="",
            )
            wait_for_tcp_listener(client, "bench", "pod", 5201, Process(), 1)


if __name__ == "__main__":
    unittest.main()
