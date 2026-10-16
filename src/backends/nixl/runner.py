from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml

from ...benchmark.artifacts import write_json
from ...benchmark.contracts import (
    BackendResources,
    BenchmarkConfig,
    BenchmarkRunner,
    RunContext,
)
from ...benchmark.kubernetes import KubectlClient
from ...benchmark.manifest import dump_manifest
from ...benchmark.network import (
    apply_secondary_network,
    discover_addresses,
    set_container_env,
    verify_attachments,
)
from ...benchmark.process import terminate_process
from ...benchmark.resources import apply_resources
from ...benchmark.utils import (
    format_eta,
    parse_thread_spec,
    resource_overrides,
    utc_timestamp,
)
from ...common.console import Colors
from ...common.types import JsonValue


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = PROJECT_ROOT / "manifests" / "nixl-template.yaml"
DEFAULT_RUN_TIMEOUT_SECONDS = 1800
GPU_RESOURCE = "nvidia.com/gpu"
CPU_UCX_TLS = "tcp,self"
ASIO_BASE_PORT = 12345  # each nixlbench pair of a run rendezvous on its own port, counting up
PARALLELISM_MODES = ("threads", "processes")

# nixlbench flags that can be set from the profile (execution.nixl.args).
INT_ARGS = {
    "pipeline_depth": 1,
    "large_blk_iter_ftr": 1,
    "progress_threads": 0,
}
BOOL_ARGS = frozenset(
    {
        "enable_pt",
        "prepared_xfer",
        "recreate_xfer",
        "reregister_mem",
        "use_hugepages",
        "enable_vmm",
        "check_consistency",
    }
)
CHOICE_ARGS = {"op_type": ("READ", "WRITE")}

# Flags the runner derives from the profile; setting them here would conflict.
MANAGED_ARGS = {
    "runtime_type": "fixed to ASIO",
    "asio_address": "derived from the initiator pod",
    "asio_port": "assigned per nixlbench pair",
    "backend": "fixed to UCX",
    "initiator_seg_type": "fixed to DRAM",
    "target_seg_type": "fixed to DRAM",
    "total_buffer_size": "computed from dimensions.block_size/batch_size/threads",
    "start_block_size": "set with dimensions.block_size",
    "max_block_size": "set with dimensions.block_size",
    "start_batch_size": "set with dimensions.batch_size",
    "max_batch_size": "set with dimensions.batch_size",
    "num_threads": "set with dimensions.threads",
    "num_iter": "set with execution.runs",
    "warmup_iter": "set with execution.nixl.warmup_runs",
}

LARGE_BLOCK_SIZE = 1 << 20  # nixlbench reduces iterations for blocks above 1 MiB
DEFAULT_LARGE_BLK_ITER_FTR = 16

ENV_NAME = re.compile(r"(UCX|NIXL|OMP)_[A-Z0-9_]+")
UNUSED_UCX_VARIABLE = re.compile(r"unused environment variable: (\S+)")
WARN_UNUSED = "UCX_WARN_UNUSED_ENV_VARS"
NETWORK_MANAGED_ENV = "UCX_NET_DEVICES"


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
        self._processes: dict[str, list[subprocess.Popen[bytes]]] = {}
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
        run_timeout = config.parameters.get(
            "run_timeout_seconds", DEFAULT_RUN_TIMEOUT_SECONDS
        )
        if (
            isinstance(run_timeout, bool)
            or not isinstance(run_timeout, int)
            or run_timeout < 1
        ):
            raise ValueError("NIXL run_timeout_seconds must be a positive integer")
        if not isinstance(config.parameters.get("gpu", True), bool):
            raise ValueError("NIXL gpu must be true or false")
        if _parallelism(config) not in PARALLELISM_MODES:
            raise ValueError(
                "NIXL parallelism must be one of: " + ", ".join(PARALLELISM_MODES)
            )
        _validate_args(config.parameters.get("args"))
        _validate_env(config.parameters.get("env"), network_set=config.network is not None)
        self._validate_pipeline_depth(config)

    def _validate_pipeline_depth(self, config: BenchmarkConfig) -> None:
        args = config.parameters.get("args") or {}
        depth = args.get("pipeline_depth", 1)
        spec = config.parameters.get("threads_spec")
        block_max = config.parameters.get("block_size_max")
        warmup = config.parameters.get("warmup_runs")
        if depth <= 1 or not isinstance(spec, str) or not isinstance(block_max, int) or not isinstance(warmup, int):
            return
        _check_pipeline_depth(
            depth=depth,
            runs=config.runs,
            warmup_runs=warmup,
            # Each process of the "processes" mode runs a single nixlbench thread.
            thread_values=(1,) if _parallelism(config) == "processes" else parse_thread_spec(spec),
            max_block_size=block_max,
            large_blk_iter_ftr=args.get("large_blk_iter_ftr", DEFAULT_LARGE_BLK_ITER_FTR),
        )

    def render_manifest(self, config: BenchmarkConfig, destination: Path) -> None:
        self.validate(config)
        manifest = self.manifest_template.read_text(encoding="utf-8")
        replacements = {
            "__TEST_ID__": config.test_id,
            "__CLIENT_NODE__": config.target_node,
            "__SERVER_NODE__": config.initiator_node,
            "__IMAGE__": config.image,
            "__IMAGE_PULL_POLICY__": config.image_pull_policy,
        }
        missing = [marker for marker in replacements if marker not in manifest]
        if missing:
            raise ValueError(
                "NIXL manifest is missing required placeholder(s): "
                + ", ".join(missing)
            )
        for marker, value in replacements.items():
            manifest = manifest.replace(marker, value)
        remaining = [marker for marker in replacements if marker in manifest]
        if remaining:
            raise ValueError(
                "NIXL manifest still contains placeholder(s): "
                + ", ".join(remaining)
            )
        if not config.parameters.get("gpu", True):
            manifest = _without_gpu(manifest)
        if config.network is not None:
            names = self.resource_names(config)
            roles = {names["initiator_pod"]: "initiator", names["target_pod"]: "target"}
            manifest = apply_secondary_network(manifest, config.network, roles)
            # UCX would otherwise be free to carry the data over eth0.
            manifest = set_container_env(
                manifest,
                {
                    pod: {"UCX_NET_DEVICES": config.network.for_role(role).interface}
                    for pod, role in roles.items()
                },
            )
        if not config.resources.is_empty:
            manifest = apply_resources(manifest, config.resources)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(manifest, encoding="utf-8")

    def deploy(self, config: BenchmarkConfig, manifest: Path) -> BackendResources:
        self.validate(config)
        names = self.resource_names(config)
        if config.network is not None:
            verify_attachments(self.kubectl, config.namespace, config.network)
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
            addresses: dict[str, str] = {}
            if config.network is not None:
                # The ASIO rendezvous must happen on the secondary network too.
                addresses = discover_addresses(
                    self.kubectl,
                    config.namespace,
                    config.network,
                    {
                        "initiator": names["initiator_pod"],
                        "target": names["target_pod"],
                    },
                )
                address = addresses["initiator_address"]
            else:
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
                **addresses,
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
        mode = _parallelism(config)
        stem = f"{config.test_id}-t{thread_count}-run{run_number}"
        command_log = output_directory / f"{stem}-command.json"
        # "threads": one nixlbench pair running the whole sweep with N threads.
        # "processes": N single-thread pairs per (block size, batch size) point,
        # so every point is measured while all of them transfer together.
        pairs = thread_count if mode == "processes" else 1
        threads_per_pair = 1 if mode == "processes" else thread_count
        parameters = dict(config.parameters)
        parameters["num_threads"] = threads_per_pair
        parameters["num_iter"] = config.runs
        parameters["warmup_iter"] = int(parameters["warmup_runs"])
        parameters["parallelism"] = mode
        block_start = int(parameters["block_size_start"])
        block_max = int(parameters["block_size_max"])
        batch_start = int(parameters["batch_size_start"])
        batch_max = int(parameters["batch_size_max"])
        parameters["total_buffer_size"] = _minimum_total_buffer_size(
            threads_per_pair, block_max, batch_max
        )
        extra_flags = _extra_flags(config.parameters.get("args"))
        env = config.parameters.get("env")
        env_prefix = ["env", *_env_assignments(env)] if env else []
        if mode == "processes":
            sweep = [
                (block, block, batch, batch)
                for block in _powers_of_two(block_start, block_max)
                for batch in _powers_of_two(batch_start, batch_max)
            ]
        else:
            sweep = [(block_start, block_max, batch_start, batch_max)]

        # Plan every nixlbench execution up front: each gets its own ASIO port.
        points: list[dict[str, JsonValue]] = []
        port = ASIO_BASE_PORT
        for start_block, end_block, start_batch, end_batch in sweep:
            label = f"-b{end_block}-n{end_batch}" if mode == "processes" else ""
            files: list[dict[str, JsonValue]] = []
            for index in range(1, pairs + 1):
                suffix = f"{label}-p{index}" if mode == "processes" else ""
                args = self._nixl_args(
                    threads_per_pair,
                    config.runs,
                    int(parameters["warmup_runs"]),
                    _minimum_total_buffer_size(threads_per_pair, end_block, end_batch),
                    start_block,
                    end_block,
                    start_batch,
                    end_batch,
                    resources.names["initiator_ip"],
                    asio_port=port,
                )
                files.append({
                    "asio_port": port,
                    "initiator_file": f"{stem}{suffix}-initiator.log",
                    "target_file": f"{stem}{suffix}-target.log",
                    "args": [*args, *extra_flags],
                })
                port += 1
            points.append({"block_size": end_block, "batch_size": end_batch, "files": files})

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
            "network": self._network_metadata(config, resources),
            "resource_overrides": resource_overrides(config),
            "nixl_args": points[0]["files"][0]["args"],  # type: ignore[index]
            "nixl_env": dict(env) if env else {},
            "parallelism": mode,
            "started_at": utc_timestamp(),
        }
        if mode == "processes":
            metadata["processes"] = pairs
            metadata["points"] = [
                {
                    "block_size": point["block_size"],
                    "batch_size": point["batch_size"],
                    "files": [
                        {key: entry[key] for key in ("asio_port", "initiator_file", "target_file")}
                        for entry in point["files"]  # type: ignore[union-attr]
                    ],
                }
                for point in points
            ]
        else:
            first = points[0]["files"][0]  # type: ignore[index]
            metadata["initiator_file"] = first["initiator_file"]
            metadata["target_file"] = first["target_file"]
        write_json(command_log, metadata)

        run_timeout = int(
            config.parameters.get("run_timeout_seconds", DEFAULT_RUN_TIMEOUT_SECONDS)
        )
        initiator_failure = target_failure = 0
        logs: list[Path] = []
        for number, point in enumerate(points, start=1):
            files = point["files"]  # type: ignore[assignment]
            initiator_logs = [output_directory / str(entry["initiator_file"]) for entry in files]
            target_logs = [output_directory / str(entry["target_file"]) for entry in files]
            logs.extend([*initiator_logs, *target_logs])
            where = ""
            if mode == "processes":
                where = (
                    f" at block_size={_format_size(int(point['block_size']))}"  # type: ignore[arg-type]
                    f" batch_size={point['batch_size']}"
                )
                self._report(
                    context,
                    f"NIXL point {number}/{len(points)}: {pairs} process(es){where}",
                )
            try:
                statuses = self._run_group(
                    config,
                    resources,
                    context,
                    env_prefix,
                    [(entry["args"], log, target) for entry, log, target in zip(files, initiator_logs, target_logs)],
                    run_timeout,
                    config.test_id,
                )
            except subprocess.TimeoutExpired:
                metadata["timed_out"] = True
                metadata["finished_at"] = utc_timestamp()
                write_json(command_log, metadata)
                raise RuntimeError(
                    f"NIXLBench timed out after {run_timeout}s (run_timeout_seconds){where}"
                ) from None
            if any(status != 0 for status in statuses.values()):
                initiator_failure = next(
                    (s for n, s in statuses.items() if n.startswith("initiator") and s != 0), 0
                )
                target_failure = next(
                    (s for n, s in statuses.items() if n.startswith("target") and s != 0), 0
                )
                metadata["initiator_status"] = initiator_failure
                metadata["target_status"] = target_failure
                metadata["finished_at"] = utc_timestamp()
                write_json(command_log, metadata)
                reason = _first_error_line(*initiator_logs, *target_logs)
                summary = ", ".join(f"{name}={status}" for name, status in statuses.items())
                raise RuntimeError(
                    f"NIXLBench failed ({summary}){where}" + (f": {reason}" if reason else "")
                )
        metadata["initiator_status"] = 0
        metadata["target_status"] = 0
        metadata["finished_at"] = utc_timestamp()
        write_json(command_log, metadata)
        if env:
            unknown = _unknown_variables(
                *(log.read_text(encoding="utf-8", errors="replace") for log in logs)
            )
            if unknown:
                raise RuntimeError(
                    "UCX does not know the variable(s) " + ", ".join(unknown)
                    + "; check the names in execution.nixl.env"
                )
        return output_directory

    def _run_group(
        self,
        config: BenchmarkConfig,
        resources: BackendResources,
        context: RunContext | None,
        env_prefix: list[str],
        jobs: Sequence[tuple[Sequence[str], Path, Path]],
        run_timeout: int,
        process_key: str,
    ) -> dict[str, int]:
        """Run one or more nixlbench pairs side by side and wait for all of them.

        Each job is (nixlbench arguments, initiator log, target log). The
        initiators start first and must be ready before any target connects.
        Raises subprocess.TimeoutExpired, after stopping everything, if the
        pairs run longer than ``run_timeout``.
        """

        started = self._processes.setdefault(process_key, [])
        initiators = []
        for args, initiator_log, _ in jobs:
            process = self.kubectl.start_exec(
                config.namespace,
                resources.names["initiator_pod"],
                [*env_prefix, "nixlbench", *args],
                initiator_log,
            )
            started.append(process)
            initiators.append(process)
        for process, (_, initiator_log, _) in zip(initiators, jobs):
            self._wait_for_initiator(config, process, initiator_log)
        self._report(
            context,
            "NIXL initiator ready" if len(jobs) == 1 else f"{len(jobs)} NIXL initiators ready",
        )
        targets = []
        for args, _, target_log in jobs:
            process = self.kubectl.start_exec(
                config.namespace,
                resources.names["target_pod"],
                [*env_prefix, "nixlbench", *args],
                target_log,
            )
            started.append(process)
            targets.append(process)
        names: dict[str, subprocess.Popen[bytes]] = {}
        for index, (initiator, target) in enumerate(zip(initiators, targets), start=1):
            label = f" {index}" if len(jobs) > 1 else ""
            names[f"initiator{label}"] = initiator
            names[f"target{label}"] = target
        try:
            return _wait_for_all(names, run_timeout)
        except subprocess.TimeoutExpired:
            for process in reversed(started):
                terminate_process(process)
            raise

    def cleanup(
        self,
        config: BenchmarkConfig,
        resources: BackendResources | None,
    ) -> None:
        for process in reversed(self._processes.pop(config.test_id, [])):
            terminate_process(process)
        if resources is None or config.keep_resources or resources.manifest_path is None:
            return
        self.kubectl.delete_manifest(config.namespace, resources.manifest_path)

    def _network_metadata(
        self,
        config: BenchmarkConfig,
        resources: BackendResources,
    ) -> dict[str, JsonValue] | None:
        if config.network is None:
            return None
        return {
            role: {
                "attachment": config.network.for_role(role).attachment,
                "interface": config.network.for_role(role).interface,
                "address": resources.names.get(f"{role}_address"),
            }
            for role in ("initiator", "target")
        }

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
        asio_port: int = ASIO_BASE_PORT,
    ) -> list[str]:
        return [
            "--runtime_type", "ASIO",
            "--asio_address", initiator_ip,
            "--asio_port", str(asio_port),
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
        self._report(context, f"Estimated time remaining: {format_eta(eta)}")

    def _report(self, context: RunContext | None, message: str) -> None:
        colors = context.colors if context is not None and context.colors else Colors("", "", "", "", "", "")
        text = f"{colors.cyan}{message}{colors.reset}"
        if context is not None and context.report is not None:
            context.report(text)
        else:
            print(text)


def _parallelism(config: BenchmarkConfig) -> object:
    return config.parameters.get("parallelism", "threads")


def _powers_of_two(start: int, maximum: int) -> list[int]:
    """Return start, 2*start, ... up to maximum, the way nixlbench steps its sweeps."""

    values = []
    value = start
    while value <= maximum:
        values.append(value)
        value *= 2
    return values


def _format_size(value: int) -> str:
    for suffix, multiplier in (("G", 1024**3), ("M", 1024**2), ("K", 1024)):
        if value % multiplier == 0:
            return f"{value // multiplier}{suffix}"
    return f"{value}B"


def _without_gpu(manifest: str) -> str:
    """Drop the GPU request and CUDA transport so the pods run on CPU-only nodes."""

    documents = [doc for doc in yaml.safe_load_all(manifest) if doc is not None]
    for document in documents:
        if document.get("kind") != "Pod":
            continue
        for container in document.get("spec", {}).get("containers", []):
            resources = container.get("resources", {})
            for section in ("requests", "limits"):
                resources.get(section, {}).pop(GPU_RESOURCE, None)
            for variable in container.get("env", []):
                if variable.get("name") == "UCX_TLS":
                    variable["value"] = CPU_UCX_TLS
    return dump_manifest(documents)


POLL_SECONDS = 0.2
PEER_GRACE_SECONDS = 5.0


def _wait_for_all(
    processes: Mapping[str, subprocess.Popen[bytes]],
    timeout: float,
) -> dict[str, int]:
    """Wait for every process, without letting one failure hang on the others.

    nixlbench's two sides wait for each other. If one exits with an error its
    peer would block until the timeout, so after a failure the survivors get
    PEER_GRACE_SECONDS to finish and are then stopped. Raises
    subprocess.TimeoutExpired if some are still running after ``timeout``.
    """

    statuses: dict[str, int] = {}
    deadline = time.monotonic() + timeout
    failed_at: float | None = None
    while len(statuses) < len(processes):
        for name, process in processes.items():
            if name in statuses:
                continue
            try:
                statuses[name] = process.wait(timeout=POLL_SECONDS)
            except subprocess.TimeoutExpired:
                continue
        now = time.monotonic()
        if failed_at is None and any(status != 0 for status in statuses.values()):
            failed_at = now
        if len(statuses) == len(processes):
            break
        if failed_at is not None and now - failed_at >= PEER_GRACE_SECONDS:
            for name, process in processes.items():
                if name not in statuses:
                    terminate_process(process)
                    statuses[name] = process.wait()
            break
        if now > deadline:
            raise subprocess.TimeoutExpired("nixlbench", timeout)
    return {name: statuses[name] for name in processes}


def _first_error_line(*paths: Path) -> str:
    """Return the first line a log reports as an error, if any."""

    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if line.lstrip().startswith("Error"):
                return line.strip()[:300]
    return ""


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"NIXL {name} must be a mapping")
    return value


def _validate_args(value: object) -> None:
    for name, setting in _mapping(value, "args").items():
        if name in MANAGED_ARGS:
            raise ValueError(f"NIXL args.{name} is managed by the profile: {MANAGED_ARGS[name]}")
        if name in INT_ARGS:
            minimum = INT_ARGS[name]
            if isinstance(setting, bool) or not isinstance(setting, int) or setting < minimum:
                raise ValueError(f"NIXL args.{name} must be an integer >= {minimum}")
        elif name in BOOL_ARGS:
            if not isinstance(setting, bool):
                raise ValueError(f"NIXL args.{name} must be true or false")
        elif name in CHOICE_ARGS:
            if setting not in CHOICE_ARGS[name]:
                raise ValueError(
                    f"NIXL args.{name} must be one of: {', '.join(CHOICE_ARGS[name])}"
                )
        else:
            supported = sorted((*INT_ARGS, *BOOL_ARGS, *CHOICE_ARGS))
            raise ValueError(
                f"NIXL args.{name} is not supported (supported: {', '.join(supported)})"
            )


def _validate_env(value: object, *, network_set: bool) -> None:
    for name, setting in _mapping(value, "env").items():
        if ENV_NAME.fullmatch(str(name)) is None:
            raise ValueError(
                f"NIXL env.{name} must be a UCX_, NIXL_ or OMP_ variable name in capitals"
            )
        if name == NETWORK_MANAGED_ENV and network_set:
            raise ValueError(
                f"NIXL env.{name} is managed by the network section (network.*.interface)"
            )
        if isinstance(setting, bool):
            continue
        if not isinstance(setting, (str, int, float)) or "\n" in str(setting) or "\0" in str(setting):
            raise ValueError(f"NIXL env.{name} must be a string, number or boolean")


def _round_up(value: int, partition: int) -> int:
    """Round up the way nixlbench does so work divides evenly across threads."""

    return value if value % partition == 0 else value + partition - value % partition


def _check_pipeline_depth(
    *,
    depth: int,
    runs: int,
    warmup_runs: int,
    thread_values: Sequence[int],
    max_block_size: int,
    large_blk_iter_ftr: int = DEFAULT_LARGE_BLK_ITER_FTR,
) -> None:
    """Reject a pipeline depth that nixlbench would fail on.

    nixlbench splits the iterations across threads (and divides them by
    ``large_blk_iter_ftr`` for blocks above 1 MiB). If a thread runs fewer
    iterations than ``pipeline_depth`` it caps the depth and then aborts because
    the descriptors no longer divide evenly. Both the timed and the warmup
    iterations go through this, so the smallest per-thread count must be at
    least the depth.
    """

    if depth <= 1:
        return
    has_large_blocks = max_block_size > LARGE_BLOCK_SIZE
    for threads in thread_values:
        partition = threads * large_blk_iter_ftr
        timed = _round_up(runs, partition)
        warmup = _round_up(warmup_runs, partition)
        counts = {"timed iterations": timed // threads}
        if warmup > 0:
            counts["warmup iterations"] = warmup // threads
        if has_large_blocks:
            counts["timed iterations for blocks above 1 MiB"] = timed // threads // large_blk_iter_ftr
            if warmup > 0:
                counts["warmup iterations for blocks above 1 MiB"] = (
                    warmup // threads // large_blk_iter_ftr
                )
        what, limit = min(counts.items(), key=lambda item: item[1])
        if depth > limit:
            needed = depth * threads * (large_blk_iter_ftr if has_large_blocks else 1)
            raise ValueError(
                f"NIXL args.pipeline_depth {depth} exceeds the {limit} {what} that each of "
                f"{threads} thread(s) runs (runs={runs}, warmup_runs={warmup_runs}), "
                f"and nixlbench would abort. Lower pipeline_depth to at most {limit}, "
                f"or raise runs and warmup_runs to at least {needed}"
            )


def _extra_flags(args: Mapping[str, object] | None) -> list[str]:
    """Return ``--name=value`` flags (the form gflags accepts for every type)."""

    flags = []
    for name, setting in (args or {}).items():
        text = ("true" if setting else "false") if isinstance(setting, bool) else str(setting)
        flags.append(f"--{name}={text}")
    return flags


def _env_assignments(env: Mapping[str, object] | None) -> list[str]:
    """Return ``NAME=value`` items for ``env``; empty when nothing is configured.

    UCX is told to report unknown variables so that a typo cannot silently
    leave a setting unapplied.
    """

    if not env:
        return []
    values = {WARN_UNUSED: "y", **env}
    return [
        f"{name}={('y' if setting else 'n') if isinstance(setting, bool) else setting}"
        for name, setting in values.items()
    ]


def _unknown_variables(*log_texts: str) -> list[str]:
    """Return UCX variables that UCX reported as unused in the given logs."""

    names: list[str] = []
    for text in log_texts:
        for name in UNUSED_UCX_VARIABLE.findall(text):
            if name not in names:
                names.append(name)
    return names


def _minimum_total_buffer_size(
    thread_count: int,
    max_block_size: int,
    max_batch_size: int,
) -> int:
    return max_block_size * max_batch_size * thread_count
