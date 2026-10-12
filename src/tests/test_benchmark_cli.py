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
    def test_parse_config_reads_common_and_backend_parameters(self) -> None:
        profile = """\
backend: fake
name: friendly-name
test_id: test-1
nodes:
  initiator: worker-a
  target: worker-b
dimensions:
  threads: 1:4:2
execution:
  image: example/fake:latest
  runs: 3
  fake:
    runtime: ASIO
    num_iter: 1000
"""
        registry = BackendRegistry(
            (
                BackendPlugin(
                    name="fake",
                    profile_options=frozenset({"runtime", "num_iter"}),
                    profile_dimensions=frozenset({"threads"}),
                ),
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile.yaml"
            profile_path.write_text(profile, encoding="utf-8")
            config = parse_config(["--profile", str(profile_path)], registry)
        self.assertEqual(config.parameters["threads"], 1)
        self.assertEqual(config.parameters["threads_spec"], "1:4:2")
        self.assertEqual(config.parameters["runtime"], "ASIO")
        self.assertEqual(config.parameters["num_iter"], 1000)
        self.assertEqual(config.runs, 3)
        self.assertEqual(config.name, "friendly-name")

    def test_profile_uses_declared_image(self) -> None:
        profile = """\
backend: nixl
nodes:
  initiator: worker-a
  target: worker-b
dimensions:
  threads: 1
  block_size: "4M:64M"
  batch_size: "1:8"
execution:
  namespace: bench
  image: example/nixl:latest
  nixl:
    warmup_runs: 10
"""
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "nixl.yaml"
            profile_path.write_text(profile, encoding="utf-8")
            config = parse_config(["--profile", str(profile_path)])
        self.assertEqual(config.image, "example/nixl:latest")
        self.assertEqual(config.parameters["block_size_start"], 4194304)
        self.assertEqual(config.parameters["block_size_max"], 67108864)
        self.assertEqual(config.parameters["batch_size_start"], 1)
        self.assertEqual(config.parameters["batch_size_max"], 8)

    def test_nixl_dimensions_require_power_of_two_ranges(self) -> None:
        profile = """\
backend: nixl
nodes:
  initiator: worker-a
  target: worker-b
dimensions:
  threads: 1
  block_size: "3M:64M"
  batch_size: "1:8"
execution:
  image: example/nixl:latest
  nixl:
    warmup_runs: 10
"""
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "invalid-nixl.yaml"
            profile_path.write_text(profile, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "powers of two"):
                parse_config(["--profile", str(profile_path)])

    def test_missing_image_is_rejected(self) -> None:
        profile = """\
backend: iperf3
nodes:
  initiator: worker-a
  target: worker-b
execution: {}
"""
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "missing-image.yaml"
            profile_path.write_text(profile, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "execution.image"):
                parse_config(["--profile", str(profile_path)])

    def test_default_name_uses_test_id(self) -> None:
        profile = """\
backend: iperf3
test_id: test-1
nodes:
  initiator: worker-a
  target: worker-b
execution:
  image: example/iperf3:latest
"""
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "iperf3.yaml"
            profile_path.write_text(profile, encoding="utf-8")
            config = parse_config(["--profile", str(profile_path)])
        self.assertEqual(config.output_name, "test-1")

    def test_parse_iperf3_duration_options(self) -> None:
        profile = """\
backend: iperf3
nodes:
  initiator: worker-a
  target: worker-b
dimensions:
  threads: 1
execution:
  image: example/iperf3:latest
  iperf3:
    duration_seconds: 5
    warmup_seconds: 2
"""
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "iperf3.yaml"
            profile_path.write_text(profile, encoding="utf-8")
            config = parse_config(["--profile", str(profile_path)])
        self.assertEqual(config.parameters["duration_seconds"], 5)
        self.assertEqual(config.parameters["warmup_seconds"], 2)

    def test_main_dispatches_through_registry(self) -> None:
        registry = BackendRegistry(
            (BackendPlugin(name="fake", runner_factory=CliRunner),)
        )
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "fake.yaml"
            profile_path.write_text(
                f"""\
backend: fake
test_id: test-1
nodes:
  initiator: worker-a
  target: worker-b
execution:
  image: example/fake:latest
  log_directory: {directory}
""",
                encoding="utf-8",
            )
            status = main(["--profile", str(profile_path)], registry)
        self.assertEqual(status, 0)

    def test_equal_nodes_are_rejected(self) -> None:
        profile = """\
backend: iperf3
nodes:
  initiator: worker-a
  target: worker-a
execution:
  image: example/iperf3:latest
"""
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "invalid.yaml"
            profile_path.write_text(profile, encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_config(["--profile", str(profile_path)])


if __name__ == "__main__":
    unittest.main()
