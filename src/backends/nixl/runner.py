from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

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
DEFAULT_PARAMETERS: dict[str, JsonValue] = {
    "runtime_type": "ASIO",
    "backend": "UCX",
    "initiator_seg_type": "DRAM",
    "target_seg_type": "DRAM",
    "total_buffer_size": 8589934592,
    "start_block_size": 4194304,
    "max_block_size": 67108864,
    "start_batch_size": 1,
    "max_batch_size": 8,
    "num_iter": 50,
    "warmup_iter": 10,
}


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
        runtime = config.parameters.get("runtime_type", "ASIO")
        if not isinstance(runtime, str) or not runtime:
            raise ValueError("NIXL runtime_type must be a non-empty string")
        backend = config.parameters.get("backend", "UCX")
        if not isinstance(backend, str) or not backend:
            raise ValueError("NIXL backend must be a non-empty string")

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
        output_directory.mkdir(parents=True, exist_ok=True)
        thread_count = int(config.parameters.get("threads", 1))
        run_number = int(config.parameters.get("run_number", 1))
        stem = f"{config.test_id}-t{thread_count}-run{run_number}"
        initiator_log = output_directory / f"{stem}-initiator.log"
        target_log = output_directory / f"{stem}-target.log"
        command_log = output_directory / f"{stem}-command.json"
        parameters = dict(DEFAULT_PARAMETERS)
        parameters.update(config.parameters)
        parameters["num_threads"] = thread_count
        args = self._nixl_args(parameters, resources.names["initiator_ip"])
        metadata: dict[str, JsonValue] = {
            "backend": self.name,
            "benchmark": config.benchmark,
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
        parameters: Mapping[str, JsonValue],
        initiator_ip: str,
    ) -> list[str]:
        args: list[str] = [
            "--runtime_type", str(parameters["runtime_type"]),
            "--asio_address", initiator_ip,
            "--asio_port", str(parameters.get("asio_port", 12345)),
        ]
        for name, value in parameters.items():
            if name in {"threads", "threads_spec", "run_number", "runtime_type", "asio_port"}:
                continue
            args.extend([f"--{name}", str(value)])
        return args

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

    def _report(self, context: RunContext | None, message: str) -> None:
        colors = context.colors if context is not None and context.colors else Colors("", "", "", "", "", "")
        text = f"{colors.cyan}{message}{colors.reset}"
        if context is not None and context.report is not None:
            context.report(text)
        else:
            print(text)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
