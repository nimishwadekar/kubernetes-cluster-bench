from __future__ import annotations

import unittest

import yaml

from src.benchmark.resources import PodResources, apply_resources, quantity

MANIFEST = """\
apiVersion: v1
kind: Service
metadata:
  name: svc
---
apiVersion: v1
kind: Pod
metadata:
  name: pod-a
spec:
  containers:
    - name: c
      image: x
      resources:
        requests: {cpu: "1", memory: 1Gi, nvidia.com/gpu: "1"}
        limits: {cpu: "8", memory: 16Gi, nvidia.com/gpu: "1"}
---
apiVersion: v1
kind: Pod
metadata:
  name: pod-b
spec:
  containers:
    - name: c
      image: x
"""


def pods(text: str) -> dict:
    return {d["metadata"]["name"]: d for d in yaml.safe_load_all(text) if d["kind"] == "Pod"}


class QuantityTests(unittest.TestCase):
    def test_cpu(self) -> None:
        self.assertEqual(quantity("cpu", "2"), 2)
        self.assertEqual(quantity("cpu", "500m"), 0.5)
        self.assertEqual(quantity("cpu", "0.5"), 0.5)
        for bad in ("", "-1", "2cores", "m", "1Gi"):
            with self.assertRaises(ValueError, msg=bad):
                quantity("cpu", bad)

    def test_memory(self) -> None:
        self.assertEqual(quantity("memory", "1Gi"), 1024**3)
        self.assertEqual(quantity("memory", "512Mi"), 512 * 1024**2)
        self.assertEqual(quantity("memory", "1G"), 10**9)
        self.assertEqual(quantity("memory", "1024"), 1024)
        for bad in ("", "1GB", "-1Gi", "Gi", "1.5.1Gi"):
            with self.assertRaises(ValueError, msg=bad):
                quantity("memory", bad)


class PodResourcesTests(unittest.TestCase):
    def test_validation(self) -> None:
        PodResources({"cpu": "2", "memory": "4Gi"}, {"cpu": "2"})
        self.assertTrue(PodResources().is_empty)
        with self.assertRaisesRegex(ValueError, "ephemeral-storage"):
            PodResources({"ephemeral-storage": "1Gi"})
        with self.assertRaisesRegex(ValueError, "limits.memory"):
            PodResources(limits={"memory": "lots"})

    def test_apply_overrides_only_given_values_in_every_pod(self) -> None:
        rendered = apply_resources(
            MANIFEST, PodResources({"cpu": "2"}, {"cpu": "2", "memory": "4Gi"})
        )
        docs = pods(rendered)
        resources = docs["pod-a"]["spec"]["containers"][0]["resources"]
        self.assertEqual(resources["requests"], {"cpu": "2", "memory": "1Gi", "nvidia.com/gpu": "1"})
        self.assertEqual(resources["limits"], {"cpu": "2", "memory": "4Gi", "nvidia.com/gpu": "1"})
        # A container with no resources section gets the given values only.
        other = docs["pod-b"]["spec"]["containers"][0]["resources"]
        self.assertEqual(other, {"requests": {"cpu": "2"}, "limits": {"cpu": "2", "memory": "4Gi"}})
        self.assertEqual(
            [d["kind"] for d in yaml.safe_load_all(rendered)], ["Service", "Pod", "Pod"]
        )

    def test_request_above_limit_is_rejected_including_manifest_defaults(self) -> None:
        with self.assertRaisesRegex(ValueError, "cpu request 4 exceeds limit 2"):
            apply_resources(MANIFEST, PodResources({"cpu": "4"}, {"cpu": "2"}))
        # The manifest's own limit (8 cpus) counts when only the request is given.
        with self.assertRaisesRegex(ValueError, "cpu request 16 exceeds limit 8"):
            apply_resources(MANIFEST, PodResources({"cpu": "16"}))
        with self.assertRaisesRegex(ValueError, "memory request 32Gi exceeds limit 16Gi"):
            apply_resources(MANIFEST, PodResources({"memory": "32Gi"}))
        apply_resources(MANIFEST, PodResources({"cpu": "8"}, {"cpu": "8"}))  # equal is fine

    def test_equal_requests_and_limits_give_guaranteed_values(self) -> None:
        rendered = apply_resources(
            MANIFEST,
            PodResources({"cpu": "4", "memory": "8Gi"}, {"cpu": "4", "memory": "8Gi"}),
        )
        resources = pods(rendered)["pod-a"]["spec"]["containers"][0]["resources"]
        for name in ("cpu", "memory"):
            self.assertEqual(resources["requests"][name], resources["limits"][name])


if __name__ == "__main__":
    unittest.main()
