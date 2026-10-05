from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypeAlias

from .config import BenchmarkConfig
from .kubernetes import ResourceNames


MetadataValue: TypeAlias = str | int
Metadata = dict[str, MetadataValue]


def write_json_metadata(metadata_log: Path, metadata: Metadata) -> None:
    """Write metadata atomically using a project-local sidecar file."""

    temporary_path = metadata_log.with_name(f".{metadata_log.name}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2)
            handle.write("\n")
        os.replace(temporary_path, metadata_log)
    finally:
        temporary_path.unlink(missing_ok=True)


def initial_metadata(
    config: BenchmarkConfig,
    resources: ResourceNames,
    kube_context: str,
    duration_display: str,
    transfer_size_display: str,
    started_at: str,
) -> Metadata:
    """Build the metadata object written before kubectl apply."""

    return {
        "run_id": config.run_id,
        "started_at": started_at,
        "kube_context": kube_context,
        "client_node": config.client_node,
        "server_node": config.server_node,
        "namespace": config.namespace,
        "logs_dir": config.log_dir,
        "image": config.image,
        "protocol": "TCP",
        "duration": duration_display,
        "transfer_size": transfer_size_display,
        "threads": config.threads,
        "service_name": resources.server_service,
        "server_pod": resources.server_pod,
        "client_pod": resources.client_pod,
    }
