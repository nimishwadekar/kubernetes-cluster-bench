from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, cast


JsonObject = Mapping[str, object]
MIN_THROUGHPUT_INTERVAL_SECONDS = 0.5
MAX_THROUGHPUT_INTERVAL_DURATION_MISMATCH_SECONDS = 0.1


@dataclass(frozen=True)
class Metadata:
    test_id: str
    started_at: str
    finished_at: str | None
    kube_context: str
    client_node: str
    server_node: str
    namespace: str
    image: str
    protocol: str
    duration_seconds: int | None
    warmup_seconds: int | None
    transfer_size: str | None
    transfer_size_bytes: int | None
    threads: int
    run_number: int | None
    run_count: int | None
    service_name: str
    server_pod: str
    client_pod: str
    client_status: int | None
    trailing_content: bool
    source_path: Path


@dataclass(frozen=True)
class ClientBenchmark:
    receiver_throughput_bps: float | None
    sender_throughput_bps: float | None
    server_throughput_mean_bps: float | None
    server_throughput_stddev_bps: float | None
    received_bytes: int | None
    sent_bytes: int | None
    test_duration_seconds: float | None
    retransmits: int | None
    thread_count: int | None
    mean_rtt_us: float | None
    host_cpu_percent: float | None
    remote_cpu_percent: float | None
    sender_congestion: str | None
    receiver_congestion: str | None
    error: str | None
    trailing_content: bool
    non_finite_fields: tuple[str, ...]
    source_path: Path

    @property
    def successful(self) -> bool:
        return self.error is None and self.receiver_throughput_bps is not None


@dataclass(frozen=True)
class BenchmarkRecord:
    metadata: Metadata
    client: ClientBenchmark


def _load_json_object(path: Path) -> tuple[JsonObject, bool]:
    try:
        text = path.read_text(encoding="utf-8")
        try:
            value: object = json.loads(text)
            trailing_content = False
        except json.JSONDecodeError:
            stripped_text = text.lstrip()
            value, end = json.JSONDecoder().raw_decode(stripped_text)
            trailing_content = bool(stripped_text[end:].strip())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return cast(JsonObject, value), trailing_content


def _object(value: object, field: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"expected {field} to be a JSON object")
    return cast(JsonObject, value)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"expected {field} to be a string")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)


def _float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"expected {field} to be numeric")
    return float(value)


def _optional_float(value: object, field: str) -> float | None:
    if value is None:
        return None
    return _float(value, field)


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected {field} to be an integer")
    return value


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _int(value, field)


def _optional_size_bytes(value: str | None) -> int | None:
    if value is None or value in {"", "N/A"}:
        return None
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}
    suffix = value[-1].upper() if value else ""
    if suffix in units:
        number = value[:-1]
        if not number.isdigit():
            raise ValueError(f"invalid transfer size: {value}")
        return int(number) * units[suffix]
    if value.isdigit():
        return int(value)
    raise ValueError(f"invalid transfer size: {value}")


def _duration_seconds(value: object) -> int | None:
    text = _string(value, "duration")
    if text == "N/A":
        return None
    first_word = text.split(maxsplit=1)[0]
    if not first_word.isdigit():
        raise ValueError(f"invalid duration: {text}")
    return int(first_word)


def load_metadata(path: Path) -> Metadata:
    root, trailing_content = _load_json_object(path)
    transfer_size = _optional_string(root.get("transfer_size"), "transfer_size")
    return Metadata(
        test_id=_string(root.get("test_id"), "test_id"),
        started_at=_string(root.get("started_at"), "started_at"),
        finished_at=_optional_string(root.get("finished_at"), "finished_at"),
        kube_context=_string(root.get("kube_context"), "kube_context"),
        client_node=_string(root.get("client_node"), "client_node"),
        server_node=_string(root.get("server_node"), "server_node"),
        namespace=_string(root.get("namespace"), "namespace"),
        image=_string(root.get("image"), "image"),
        protocol=_string(root.get("protocol"), "protocol"),
        duration_seconds=_duration_seconds(root.get("duration")),
        warmup_seconds=_optional_int(root.get("warmup"), "warmup"),
        transfer_size=transfer_size,
        transfer_size_bytes=_optional_size_bytes(transfer_size),
        threads=_int(root.get("threads", root.get("parallel_streams")), "threads"),
        run_number=_optional_int(
            root.get("run_id", root.get("run")), "run_id"
        ),
        run_count=_optional_int(root.get("runs"), "runs"),
        service_name=_string(root.get("service_name"), "service_name"),
        server_pod=_string(root.get("server_pod"), "server_pod"),
        client_pod=_string(root.get("client_pod"), "client_pod"),
        client_status=_optional_int(root.get("client_status"), "client_status"),
        trailing_content=trailing_content,
        source_path=path,
    )


def _mean_rtt_us(end: JsonObject) -> float | None:
    threads_value = end.get("streams")
    if not isinstance(threads_value, list):
        return None
    thread_values = cast(list[object], threads_value)
    rtts: list[float] = []
    for thread_value in thread_values:
        thread = _object(thread_value, "end.streams item")
        sender = thread.get("sender")
        if not isinstance(sender, dict):
            continue
        sender_object = cast(JsonObject, sender)
        mean_rtt = _optional_float(sender_object.get("mean_rtt"), "mean_rtt")
        if mean_rtt is not None:
            rtts.append(mean_rtt)
    return sum(rtts) / len(rtts) if rtts else None


def _server_throughput_stats(
    root: JsonObject,
) -> tuple[float | None, float | None]:
    server_output_value = root.get("server_output_json")
    if server_output_value is None:
        return None, None
    server_output = _object(server_output_value, "server_output_json")
    intervals_value = server_output.get("intervals")
    if not isinstance(intervals_value, list):
        raise ValueError("expected server_output_json.intervals to be a list")

    values: list[float] = []
    for index, interval_value in enumerate(cast(list[object], intervals_value)):
        interval = _object(interval_value, f"server_output_json.intervals[{index}]")
        interval_sum = _object(
            interval.get("sum"), f"server_output_json.intervals[{index}].sum"
        )
        omitted = interval_sum.get("omitted")
        if omitted is True:
            continue
        start = _optional_float(
            interval_sum.get("start"),
            f"server_output_json.intervals[{index}].sum.start",
        )
        end = _optional_float(
            interval_sum.get("end"),
            f"server_output_json.intervals[{index}].sum.end",
        )
        seconds = _optional_float(
            interval_sum.get("seconds"),
            f"server_output_json.intervals[{index}].sum.seconds",
        )
        bits_per_second = _optional_float(
            interval_sum.get("bits_per_second"),
            f"server_output_json.intervals[{index}].sum.bits_per_second",
        )
        if (
            start is not None
            and end is not None
            and seconds is not None
            and end >= start
            and seconds >= MIN_THROUGHPUT_INTERVAL_SECONDS
            and abs(seconds - (end - start))
            <= MAX_THROUGHPUT_INTERVAL_DURATION_MISMATCH_SECONDS
            and bits_per_second is not None
        ):
            values.append(bits_per_second)

    if not values:
        return None, None
    mean = statistics.fmean(values)
    standard_deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean, standard_deviation


def load_client_benchmark(path: Path) -> ClientBenchmark:
    root, trailing_content = _load_json_object(path)
    start = _object(root.get("start", {}), "start")
    test_start = _object(start.get("test_start", {}), "start.test_start")
    end = _object(root.get("end", {}), "end")
    sum_sent = _object(end.get("sum_sent", {}), "end.sum_sent")
    sum_received = _object(end.get("sum_received", {}), "end.sum_received")
    cpu = _object(end.get("cpu_utilization_percent", {}), "end.cpu_utilization_percent")
    server_throughput_mean_bps, server_throughput_stddev_bps = _server_throughput_stats(
        root
    )

    client = ClientBenchmark(
        receiver_throughput_bps=_optional_float(
            sum_received.get("bits_per_second"), "end.sum_received.bits_per_second"
        ),
        sender_throughput_bps=_optional_float(
            sum_sent.get("bits_per_second"), "end.sum_sent.bits_per_second"
        ),
        server_throughput_mean_bps=server_throughput_mean_bps,
        server_throughput_stddev_bps=server_throughput_stddev_bps,
        received_bytes=_optional_int(sum_received.get("bytes"), "end.sum_received.bytes"),
        sent_bytes=_optional_int(sum_sent.get("bytes"), "end.sum_sent.bytes"),
        test_duration_seconds=_optional_float(
            sum_received.get("seconds"), "end.sum_received.seconds"
        ),
        retransmits=_optional_int(sum_sent.get("retransmits"), "end.sum_sent.retransmits"),
        thread_count=_optional_int(test_start.get("num_streams"), "start.test_start.num_streams"),
        mean_rtt_us=_mean_rtt_us(end),
        host_cpu_percent=_optional_float(
            cpu.get("host_total"), "end.cpu_utilization_percent.host_total"
        ),
        remote_cpu_percent=_optional_float(
            cpu.get("remote_total"), "end.cpu_utilization_percent.remote_total"
        ),
        sender_congestion=_optional_string(
            end.get("sender_tcp_congestion"), "end.sender_tcp_congestion"
        ),
        receiver_congestion=_optional_string(
            end.get("receiver_tcp_congestion"), "end.receiver_tcp_congestion"
        ),
        error=_optional_string(root.get("error"), "error"),
        trailing_content=trailing_content,
        non_finite_fields=(),
        source_path=path,
    )
    numeric_fields = {
        "receiver_throughput_bps": client.receiver_throughput_bps,
        "sender_throughput_bps": client.sender_throughput_bps,
        "test_duration_seconds": client.test_duration_seconds,
        "mean_rtt_us": client.mean_rtt_us,
        "host_cpu_percent": client.host_cpu_percent,
        "remote_cpu_percent": client.remote_cpu_percent,
    }
    non_finite_fields = tuple(
        name for name, value in numeric_fields.items()
        if value is not None and not math.isfinite(value)
    )
    return replace(client, non_finite_fields=non_finite_fields)


def client_path_for_metadata(metadata_path: Path) -> Path:
    suffix = "-metadata.json"
    if not metadata_path.name.endswith(suffix):
        raise ValueError(f"metadata filename must end with {suffix}: {metadata_path}")
    run_prefix = metadata_path.name[: -len(suffix)]
    return metadata_path.with_name(f"{run_prefix}-client.json")


def load_benchmark_record(metadata_path: Path) -> BenchmarkRecord:
    metadata = load_metadata(metadata_path)
    client_path = client_path_for_metadata(metadata_path)
    if not client_path.is_file():
        raise ValueError(f"client result not found for {metadata_path}: {client_path}")
    return BenchmarkRecord(metadata=metadata, client=load_client_benchmark(client_path))
