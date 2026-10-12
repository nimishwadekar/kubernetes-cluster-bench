from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from src.backends.plugin import BackendPlugin
from src.backends.registry import BackendRegistry
from src.benchmark.artifacts import BenchmarkExecution
from src.benchmark.contracts import (
    BackendResources,
    BenchmarkConfig,
    RunContext,
)
from src.benchmark.orchestrator import parse_thread_spec, run_benchmark


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []
        self.contexts: list[RunContext | None] = []
        self.cleaned = False
        self.deployed = False

    def validate(self, config: BenchmarkConfig) -> None:
        if config.backend != "fake":
            raise ValueError("unexpected backend")

    def render_manifest(self, config: BenchmarkConfig, destination: Path) -> None:
        destination.write_text("kind: Pod\n", encoding="utf-8")

    def deploy(self, config: BenchmarkConfig, manifest: Path) -> BackendResources:
        self.deployed = True
        return BackendResources(
            names={"pod": "fake-pod"}, manifest_path=manifest
        )

    def execute(
        self,
        config: BenchmarkConfig,
        resources: BackendResources,
        output_directory: Path,
        context: RunContext | None = None,
    ) -> Path:
        thread_count = int(config.parameters["threads"])
        run_number = int(config.parameters["run_number"])
        self.calls.append((thread_count, run_number))
        self.contexts.append(context)
        output_directory.joinpath("raw.txt").write_text(
            f"threads={thread_count};run={run_number}\n", encoding="utf-8"
        )
        return output_directory

    def cleanup(
        self,
        config: BenchmarkConfig,
        resources: BackendResources | None,
    ) -> None:
        self.cleaned = True


class FailingRunner(FakeRunner):
    def execute(
        self,
        config: BenchmarkConfig,
        resources: BackendResources,
        output_directory: Path,
        context: RunContext | None = None,
    ) -> Path:
        if config.parameters["threads"] == 2:
            raise RuntimeError("simulated run failure")
        return super().execute(config, resources, output_directory, context)


class BenchmarkFrontendTests(unittest.TestCase):
    def make_config(self, directory: Path, runs: int = 2) -> BenchmarkConfig:
        return BenchmarkConfig(
            backend="fake",
            test_id="test-1",
            initiator_node="worker-a",
            target_node="worker-b",
            namespace="bench",
            image="example/image:latest",
            log_directory=directory,
            runs=runs,
            parameters={"threads": 1, "threads_spec": "1:2"},
        )

    def test_thread_spec_is_inclusive(self) -> None:
        self.assertEqual(parse_thread_spec("1:5:2"), (1, 3, 5))

    def test_frontend_selects_registry_plugin_and_writes_raw_layout(self) -> None:
        runner = FakeRunner()
        registry = BackendRegistry(
            (BackendPlugin(name="fake", runner_factory=lambda: runner),)
        )
        with tempfile.TemporaryDirectory() as directory:
            execution = run_benchmark(self.make_config(Path(directory)), registry)
            self.assertIsInstance(execution, BenchmarkExecution)
            self.assertEqual(runner.calls, [(1, 1), (1, 2), (2, 1), (2, 2)])
            self.assertTrue(runner.deployed)
            self.assertTrue(runner.cleaned)
            self.assertTrue(execution.manifest_path.is_file())
            self.assertEqual(len(execution.outcomes), 4)
            self.assertTrue(execution.successful)
            self.assertTrue(all(context is not None for context in runner.contexts))
            first_context = runner.contexts[0]
            assert first_context is not None
            self.assertGreater(first_context.benchmark_started_monotonic, 0)
            self.assertFalse((execution.test_directory / "normalized").exists())
            for outcome in execution.outcomes:
                self.assertTrue(outcome.raw_directory.joinpath("raw.txt").is_file())

    def test_optional_name_controls_log_directory(self) -> None:
        runner = FakeRunner()
        registry = BackendRegistry(
            (BackendPlugin(name="fake", runner_factory=lambda: runner),)
        )
        with tempfile.TemporaryDirectory() as directory:
            config = replace(self.make_config(Path(directory)), name="friendly-name")
            execution = run_benchmark(config, registry)
        self.assertEqual(execution.test_directory.name, "friendly-name")
        self.assertEqual(execution.test_id, "test-1")

    def test_backend_managed_repetitions_execute_once_per_dimension(self) -> None:
        runner = FakeRunner()
        registry = BackendRegistry(
            (
                BackendPlugin(
                    name="fake",
                    runner_factory=lambda: runner,
                    framework_repeats=False,
                ),
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            execution = run_benchmark(
                self.make_config(Path(directory), runs=3), registry
            )
        self.assertEqual(runner.calls, [(1, 1), (2, 1)])
        self.assertTrue(execution.successful)

    def test_frontend_continues_after_one_run_failure(self) -> None:
        runner = FailingRunner()
        registry = BackendRegistry(
            (BackendPlugin(name="fake", runner_factory=lambda: runner),)
        )
        with tempfile.TemporaryDirectory() as directory:
            execution = run_benchmark(self.make_config(Path(directory), runs=1), registry)
        self.assertEqual(len(execution.outcomes), 2)
        self.assertFalse(execution.successful)
        self.assertEqual(execution.outcomes[1].error, "simulated run failure")
        self.assertTrue(runner.cleaned)


if __name__ == "__main__":
    unittest.main()
