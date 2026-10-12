from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from ...benchmark.artifacts import write_json
from ...benchmark.contracts import (
    BackendResources,
    BenchmarkConfig,
    BenchmarkRunner,
    RunContext,
)
from ...benchmark.kubernetes import KubectlClient
from ...benchmark.process import terminate_process
from ...common.console import Colors
from ...common.types import JsonValue


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = PROJECT_ROOT / "nixl" / "nixl-benchmark.yaml"
class NixlRunner(BenchmarkRunner):
    """Run NIXLBench initiator and target processes on two GPU pods."""

    name = "nixl"

    def __init__(
        self,
        manifest_template: Path = DEFAULT_MANIFEST,
        kubectl: KubectlClient | None = None,
    ) -> None:
        self.manifest_template = manifest_template
        self.kubectl = kubectl or KubectlClient()
        self._processes: dict[str, tuple[subprocess.Popen[bytes], subprocess.Popen[bytes] | None]] = {}
        self._colors: Colors | None = None

    def validate(self, config: BenchmarkConfig) -> None:
        if not self.manifest_template.is_file():
            raise ValueError(f"NIXL manifest not found: {self.manifest_template}")
        warmup_runs = config.parameters.get("warmup_runs")
        if (
            isinstance(warmup_runs, bool)
            or not isinstance(warmup_runs, int)
            or warmup_runs < 0
        ):
            raise ValueError("NIXL warmup_runs must be a non-negative integer")

    def render_manifest(self, config: BenchmarkConfig, destination: Path) -> None:
        self.validate(config)
        manifest = self.manifest_template.read_text(encoding="utf-8")
        replacements = {
            "__TEST_ID__": config.test_id,
            "__CLIENT_NODE__": config.target_node,
            "__SERVER_NODE__": config.initiator_node,
            "__IMAGE__": config.image,
        }
        missing = [marker for marker in replacements if marker not in manifest]
        if missing:
            raise ValueError(
                "NIXL manifest is missing required placeholder(s): "
                + ", ".join(missing)
            )
        for marker, value in replacements.items():
            manifest = manifest.replace(marker, value)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(manifest, encoding="utf-8")

    def deploy(self, config: BenchmarkConfig, manifest: Path) -> BackendResources:
        self.validate(config)
        names = self.resource_names(config)
        for resource in names["collision_targets"].split(","):
            if self.kubectl.resource_exists(resource, config.namespace):
                raise ValueError(
                    f"resource already exists: {resource}; retry with a clean namespace"
                )
        try:
            self.kubectl.apply_manifest(config.namespace, manifest)
            for pod in (names["initiator_pod"], names["target_pod"]):
                self.kubectl.wait_for(
                    config.namespace,
                    f"pod/{pod}",
                    "jsonpath={.status.phase}=Running",
                    config.timeout_seconds,
                )
            address = self.kubectl.output(
                [
                    "get",
                    "pod",
                    names["initiator_pod"],
                    "-n",
                    config.namespace,
                    "-o",
                    "jsonpath={.status.podIP}",
                ]
            )
            if not address:
                raise ValueError("unable to determine the NIXL initiator pod IP")
        except BaseException:
            self.kubectl.delete_manifest(config.namespace, manifest)
            raise
        return BackendResources(
            names={
                "initiator_pod": names["initiator_pod"],
                "target_pod": names["target_pod"],
                "initiator_ip": address,
            },
            manifest_path=manifest,
        )

    def execute(
        self,
        config: BenchmarkConfig,
        resources: BackendResources,
        output_directory: Path,
        context: RunContext | None = None,
    ) -> Path:
        self.validate(config)
        self._colors = context.colors if context is not None else self._colors
        self._report_eta(config, context)
        output_directory.mkdir(parents=True, exist_ok=True)
        thread_count = int(config.parameters.get("threads", 1))
        run_number = int(config.parameters.get("run_number", 1))
        stem = f"{config.test_id}-t{thread_count}-run{run_number}"
        initiator_log = output_directory / f"{stem}-initiator.log"
        target_log = output_directory / f"{stem}-target.log"
        command_log = output_directory / f"{stem}-command.json"
        parameters = dict(config.parameters)
        parameters["num_threads"] = thread_count
        parameters["num_iter"] = config.runs
        parameters["warmup_iter"] = int(parameters["warmup_runs"])
        total_buffer_size = _minimum_total_buffer_size(
            thread_count,
            int(parameters["block_size_max"]),
            int(parameters["batch_size_max"]),
        )
        parameters["total_buffer_size"] = total_buffer_size
        args = self._nixl_args(
            thread_count,
            config.runs,
            int(parameters["warmup_runs"]),
            total_buffer_size,
            int(parameters["block_size_start"]),
            int(parameters["block_size_max"]),
            int(parameters["batch_size_start"]),
            int(parameters["batch_size_max"]),
            resources.names["initiator_ip"],
        )
        metadata: dict[str, JsonValue] = {
            "backend": self.name,
            "test_id": config.test_id,
            "run_number": run_number,
            "kube_context": config.kube_context,
            "initiator_node": config.initiator_node,
            "target_node": config.target_node,
            "namespace": config.namespace,
            "image": config.image,
            "parameters": parameters,
            "nixl_args": args,
            "initiator_file": initiator_log.name,
            "target_file": target_log.name,
            "started_at": _utc_timestamp(),
        }
        write_json(command_log, metadata)

        initiator = self.kubectl.start_exec(
            config.namespace,
            resources.names["initiator_pod"],
            ["nixlbench", *args],
            initiator_log,
        )
        self._processes[config.test_id] = (initiator, None)
        self._wait_for_initiator(config, initiator, initiator_log)
        self._report(context, "NIXL initiator ready")

        target = self.kubectl.start_exec(
            config.namespace,
            resources.names["target_pod"],
            ["nixlbench", *args],
            target_log,
        )
        self._processes[config.test_id] = (initiator, target)
        target_status = target.wait()
        initiator_status = initiator.wait()
        metadata["initiator_status"] = initiator_status
        metadata["target_status"] = target_status
        metadata["finished_at"] = _utc_timestamp()
        write_json(command_log, metadata)
        if initiator_status != 0 or target_status != 0:
            raise RuntimeError(
                f"NIXLBench failed (initiator={initiator_status}, target={target_status})"
            )
        return output_directory

    def cleanup(
        self,
        config: BenchmarkConfig,
        resources: BackendResources | None,
    ) -> None:
        processes = self._processes.pop(config.test_id, None)
        if processes is not None:
            terminate_process(processes[1])
            terminate_process(processes[0])
        if resources is None or config.keep_resources or resources.manifest_path is None:
            return
        self.kubectl.delete_manifest(config.namespace, resources.manifest_path)

    def resource_names(self, config: BenchmarkConfig) -> dict[str, str]:
        initiator = f"nixlbench-server-{config.test_id}"
        target = f"nixlbench-client-{config.test_id}"
        return {
            "initiator_pod": initiator,
            "target_pod": target,
            "collision_targets": f"pod/{initiator},pod/{target}",
        }

    def _nixl_args(
        self,
        thread_count: int,
        iteration_count: int,
        warmup_count: int,
        total_buffer_size: int,
        block_size_start: int,
        block_size_max: int,
        batch_size_start: int,
        batch_size_max: int,
        initiator_ip: str,
    ) -> list[str]:
        return [
            "--runtime_type", "ASIO",
            "--asio_address", initiator_ip,
            "--asio_port", "12345",
            "--backend", "UCX",
            "--initiator_seg_type", "DRAM",
            "--target_seg_type", "DRAM",
            "--total_buffer_size", str(total_buffer_size),
            "--start_block_size", str(block_size_start),
            "--max_block_size", str(block_size_max),
            "--start_batch_size", str(batch_size_start),
            "--max_batch_size", str(batch_size_max),
            "--num_threads", str(thread_count),
            "--num_iter", str(iteration_count),
            "--warmup_iter", str(warmup_count),
        ]

    def _wait_for_initiator(
        self,
        config: BenchmarkConfig,
        process: subprocess.Popen[bytes],
        log_path: Path,
    ) -> None:
        deadline = time.monotonic() + config.timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("NIXL initiator exited before becoming ready")
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if "Using address " in text and "port " in text:
                return
            time.sleep(0.2)
        raise TimeoutError("timed out waiting for NIXL initiator readiness")

    def _report_eta(
        self,
        config: BenchmarkConfig,
        context: RunContext | None,
    ) -> None:
        if context is None:
            return
        if context.completed_runs > 0:
            elapsed = time.monotonic() - context.benchmark_started_monotonic
            eta = (
                elapsed / context.completed_runs
                * (context.total_runs - context.completed_runs)
            )
        else:
            eta = float(config.timeout_seconds * context.total_runs)
        self._report(context, f"Estimated time remaining: {_format_eta(eta)}")

    def _report(self, context: RunContext | None, message: str) -> None:
        colors = context.colors if context is not None and context.colors else Colors("", "", "", "", "", "")
        text = f"{colors.cyan}{message}{colors.reset}"
        if context is not None and context.report is not None:
            context.report(text)
        else:
            print(text)


def _minimum_total_buffer_size(
    thread_count: int,
    max_block_size: int,
    max_batch_size: int,
) -> int:
    return max_block_size * max_batch_size * thread_count


def _format_eta(seconds: float) -> str:
    remaining = max(0, int(round(seconds)))
    minutes, seconds = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
