"""Measure authenticated RemoteDSMAPI workloads and remote adapters locally."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from statistics import mean, median
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import psutil
import cv2

from application.checksum import ChecksumAdapter
from application.image_processing.image_adapter import ImageProcessingAdapter, apply_operation
from application.matrix_multiplication.matrix_adapter import MatrixMultiplicationAdapter
from compute import NetworkComputeAPI
from network.tcp.dsmapi_stub import RemoteDSMAPI
from tests.mocks.mock_dsm_api import MockDSMAPI


class ProcessMemorySampler:
    def __init__(self, processes: dict[str, int], interval: float):
        self.processes = {"client": os.getpid(),
                          **{name: int(pid) for name, pid in processes.items()}}
        self.interval = interval
        self.baseline: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        self.aggregate_peak = 0
        self._task: asyncio.Task | None = None

    def _sample(self) -> dict[str, int]:
        sample = {}
        for name, pid in self.processes.items():
            try:
                sample[name] = psutil.Process(pid).memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return sample

    async def start(self):
        self.baseline = self._sample()
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        while True:
            sample = self._sample()
            for name, value in sample.items():
                self.peak[name] = max(self.peak.get(name, value), value)
            self.aggregate_peak = max(self.aggregate_peak, sum(sample.values()))
            await asyncio.sleep(self.interval)

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.peak = {name: max(value, self.baseline.get(name, value))
                     for name, value in self.peak.items()}
        return {
            "baseline_rss_bytes_by_process": self.baseline,
            "peak_rss_bytes_by_process": self.peak,
            "aggregate_sampled_peak_rss_bytes": self.aggregate_peak,
            "sampling_interval_seconds": self.interval,
        }


async def run(args: argparse.Namespace) -> list[dict]:
    cv2.setNumThreads(args.cpu_threads)
    enrollment_secret = os.getenv("DSM_CLIENT_ENROLLMENT_SECRET")
    if not enrollment_secret:
        raise ValueError("set DSM_CLIENT_ENROLLMENT_SECRET in the process environment")
    for env_name, value in (("DSM_CA_CERT_FILE", args.ca_cert),
                            ("DSM_CLIENT_CERT_FILE", args.client_cert),
                            ("DSM_CLIENT_KEY_FILE", args.client_key)):
        if value:
            os.environ[env_name] = value
    api = RemoteDSMAPI(args.host, args.port, args.label, enrollment_secret,
                       server_hostname=args.server_name or args.host)
    rows: list[dict] = []
    allocated: list[str] = []
    allocated_all: list[str] = []
    compute_api = (NetworkComputeAPI(
        api, direct_local_reads=args.input_access_mode == "direct"
    ) if args.distributed_compute else None)
    audit_before = Path(args.audit_jsonl).stat().st_size if args.audit_jsonl and Path(args.audit_jsonl).exists() else 0
    memory_sampler = ProcessMemorySampler(dict(item.split("=", 1) for item in args.sample_process),
                                          args.memory_sample_interval)
    await memory_sampler.start()
    try:
        await asyncio.wait_for(api.connect(), timeout=15)
        for size in args.sizes:
            if size > args.max_bytes:
                raise ValueError(f"size {size} exceeds --max-bytes {args.max_bytes}")
            cluster_before = await _cluster_traffic_snapshot(api)
            network_before = api.connection.stats()
            object_id = await asyncio.wait_for(api.alloc(size), timeout=20)
            allocated.append(object_id)
            allocated_all.append(object_id)
            payload = bytes((i % 251 for i in range(size)))
            writes, reads = [], []
            for _ in range(args.repeats):
                start = perf_counter()
                await asyncio.wait_for(api.write(object_id, payload), timeout=30)
                writes.append(perf_counter() - start)
                start = perf_counter()
                result = await asyncio.wait_for(api.read(object_id), timeout=30)
                reads.append(perf_counter() - start)
                if result != payload:
                    raise AssertionError("remote benchmark payload mismatch")
            row = {"workload": "remote_read_write", "size_bytes": size,
                         "repeat_count": args.repeats, "read_latency_mean_s": mean(reads),
                         "write_latency_mean_s": mean(writes),
                         "read_throughput_bytes_s": size / mean(reads),
                         "write_throughput_bytes_s": size / mean(writes),
                         "scheduler_policy": args.scheduler_policy,
                         "security_path": "TLS+authenticated token+audit"}
            _add_network_evidence(row, api, network_before)
            await _add_cluster_network_evidence(row, api, cluster_before)
            rows.append(row)
            await asyncio.wait_for(api.free(object_id), timeout=15)
            allocated.remove(object_id)

        checksum_data = b"benchmark checksum payload"
        started = perf_counter()
        checksum = await ChecksumAdapter(api).run(checksum_data)
        rows.append({"workload": "checksum_adapter", "end_to_end_s": perf_counter() - started,
                     "correct": checksum == hashlib.sha256(checksum_data).hexdigest(),
                     "scheduler_policy": args.scheduler_policy,
                     "security_path": "TLS+authenticated token+audit"})

        rng = np.random.default_rng(args.seed)
        image = rng.integers(0, 256, (args.image_size, args.image_size + 3, 3), dtype=np.uint8)
        for operation in ("invert", "grayscale", "blur"):
            expected = apply_operation(image, operation)
            modes = [("native", None), ("local_tiled", MockDSMAPI()), ("client_compute", api)]
            if compute_api is not None:
                modes.append(("distributed_compute", api))
            for mode, dsm in modes:
                before = set(compute_api._results) if mode == "distributed_compute" else set()
                cluster_before = await _cluster_traffic_snapshot(api)
                network_before = api.connection.stats()
                adapter = None if mode == "native" else ImageProcessingAdapter(
                    dsm, operation=operation, tile_height=args.tile_size,
                    tile_width=args.tile_size, halo=2 if operation == "blur" else 0,
                    compute_api=compute_api if mode == "distributed_compute" else None,
                )
                samples = []
                for _ in range(args.application_repeats):
                    started = perf_counter()
                    output = (apply_operation(image, operation) if adapter is None
                              else await adapter.run(image))
                    samples.append(perf_counter() - started)
                    if not np.array_equal(output, expected):
                        raise AssertionError(f"{mode} {operation} image output mismatch")
                elapsed = median(samples)
                row = {"workload": f"image_{operation}", "end_to_end_s": elapsed,
                       "end_to_end_median_s": elapsed,
                       "end_to_end_min_s": min(samples),
                       "end_to_end_max_s": max(samples),
                       "application_repeat_count": len(samples),
                       "timed_operation_calls": len(samples), "correct": True,
                       "scheduler_policy": args.scheduler_policy,
                       "security_path": "TLS+authenticated token+audit" if dsm is api else "local",
                       "execution_mode": mode,
                       "input_access_mode": args.input_access_mode}
                if mode == "distributed_compute":
                    _add_compute_evidence(row, compute_api, before)
                _add_network_evidence(row, api, network_before)
                await _add_cluster_network_evidence(row, api, cluster_before)
                rows.append(row)

        a, b = rng.standard_normal((7, 5)), rng.standard_normal((5, 6))
        expected_matrix = a @ b
        modes = [("native", None), ("local_tiled", MockDSMAPI()), ("client_compute", api)]
        if compute_api is not None:
            modes.append(("distributed_compute", api))
        for mode, dsm in modes:
            before = set(compute_api._results) if mode == "distributed_compute" else set()
            cluster_before = await _cluster_traffic_snapshot(api)
            network_before = api.connection.stats()
            samples = []
            for _ in range(args.application_repeats):
                started = perf_counter()
                product = (a @ b) if mode == "native" else await MatrixMultiplicationAdapter(
                    dsm, block_size=args.block_size,
                    compute_api=compute_api if mode == "distributed_compute" else None,
                ).run((a, b))
                samples.append(perf_counter() - started)
                if not np.allclose(product, expected_matrix, rtol=1e-10, atol=1e-10):
                    raise AssertionError(f"{mode} matrix benchmark output mismatch")
            elapsed = median(samples)
            row = {"workload": "matrix_multiply", "correct": True,
                   "end_to_end_s": elapsed, "end_to_end_median_s": elapsed,
                   "end_to_end_min_s": min(samples), "end_to_end_max_s": max(samples),
                   "application_repeat_count": len(samples),
                   "timed_operation_calls": len(samples),
                   "scheduler_policy": args.scheduler_policy,
                   "security_path": "TLS+authenticated token+audit" if dsm is api else "local",
                   "execution_mode": mode,
                   "input_access_mode": args.input_access_mode}
            if mode == "distributed_compute":
                _add_compute_evidence(row, compute_api, before)
            _add_network_evidence(row, api, network_before)
            await _add_cluster_network_evidence(row, api, cluster_before)
            rows.append(row)
    finally:
        for object_id in reversed(allocated):
            await api.free(object_id)
        await api.close()
        memory_metrics = await memory_sampler.stop()

    node_logs = {str(path): Path(path).read_text(encoding="utf-8", errors="replace")
                 for path in args.node_log}
    placements = {name: sum(f"write {object_id}" in log for object_id in allocated_all)
                  for name, log in node_logs.items()}
    for row in rows:
        row.setdefault("input_access_mode", args.input_access_mode)
        row["cpu_thread_limit"] = args.cpu_threads
        row["worker_slots_per_node"] = args.worker_slots
        row["per_node_allocation_evidence"] = json.dumps(placements, sort_keys=True)
        row["nodes_with_allocations"] = sum(count > 0 for count in placements.values())
        row.update(memory_metrics)
    if args.node_stats_json:
        stats = json.loads(Path(args.node_stats_json).read_text(encoding="utf-8"))
        for row in rows:
            row["per_node_stats_json"] = json.dumps(stats, sort_keys=True)
    if args.audit_jsonl and Path(args.audit_jsonl).exists():
        after = Path(args.audit_jsonl).stat().st_size
        for row in rows:
            row["audit_bytes_added"] = after - audit_before
    native_times = {
        row["workload"]: row["end_to_end_s"] for row in rows
        if row.get("execution_mode") == "native"
    }
    for row in rows:
        if row.get("workload") in native_times and row.get("execution_mode") != "native":
            row["speedup_vs_native"] = native_times[row["workload"]] / row["end_to_end_s"]
    return rows


def _add_compute_evidence(row: dict, compute_api: NetworkComputeAPI, before: set[str]) -> None:
    results = [result for task_id, result in compute_api._results.items() if task_id not in before]
    row["worker_task_count"] = len(results)
    row["worker_node_ids"] = json.dumps(sorted({result.node_id for result in results}))
    row["worker_process_ids"] = json.dumps(sorted({result.worker_pid for result in results}))
    row["worker_execution_seconds"] = sum(
        max(0.0, result.execution_finished_at - result.execution_started_at)
        for result in results
    )
    row["dsm_input_payload_bytes"] = sum(
        result.metrics.get("dsm_input_payload_bytes", 0) for result in results
    )
    row["dsm_output_payload_bytes"] = sum(
        result.metrics.get("dsm_output_payload_bytes", 0) for result in results
    )
    row["local_read_count"] = sum(result.metrics.get("local_read_count", 0) for result in results)
    row["local_read_bytes"] = sum(result.metrics.get("local_read_bytes", 0) for result in results)
    row["remote_read_count"] = sum(result.metrics.get("remote_read_count", 0) for result in results)
    row["remote_read_bytes"] = sum(result.metrics.get("remote_read_bytes", 0) for result in results)
    for metric in (
        "local_output_write_count", "local_output_write_bytes",
        "remote_output_write_count", "remote_output_write_bytes",
        "seal_operation_count", "pin_operation_count", "release_operation_count",
    ):
        row[metric] = sum(result.metrics.get(metric, 0) for result in results)


def _add_network_evidence(row: dict, api: RemoteDSMAPI, before: dict) -> None:
    after = api.connection.stats()
    for key in ("wire_bytes_sent", "wire_bytes_received", "requests_sent",
                "responses_received", "timeouts", "call_duration_seconds"):
        row[f"client_{key}"] = after[key] - before[key]
    row["client_wire_bytes_sent_by_payload"] = json.dumps({
        name: amount - before["wire_bytes_sent_by_payload"].get(name, 0)
        for name, amount in after["wire_bytes_sent_by_payload"].items()
        if amount - before["wire_bytes_sent_by_payload"].get(name, 0)
    }, sort_keys=True)
    row["client_wire_bytes_received_by_payload"] = json.dumps({
        name: amount - before["wire_bytes_received_by_payload"].get(name, 0)
        for name, amount in after["wire_bytes_received_by_payload"].items()
        if amount - before["wire_bytes_received_by_payload"].get(name, 0)
    }, sort_keys=True)


async def _cluster_traffic_snapshot(api: RemoteDSMAPI) -> dict[str, dict]:
    return {node.node_id: {
        "sent": int(node.coordinator_wire_bytes_sent),
        "received": int(node.coordinator_wire_bytes_received),
        "sent_by_payload": dict(node.coordinator_wire_bytes_sent_by_payload),
        "received_by_payload": dict(node.coordinator_wire_bytes_received_by_payload),
    } for node in await api.get_cluster_stats()}


async def _add_cluster_network_evidence(
    row: dict, api: RemoteDSMAPI, before: dict[str, dict]
) -> None:
    after = await _cluster_traffic_snapshot(api)
    sent_by_node, received_by_node = {}, {}
    sent_by_payload, received_by_payload = {}, {}
    for node_id, current in after.items():
        prior = before.get(node_id, {})
        sent = max(0, current["sent"] - prior.get("sent", 0))
        received = max(0, current["received"] - prior.get("received", 0))
        if sent:
            sent_by_node[node_id] = sent
        if received:
            received_by_node[node_id] = received
        for payload, amount in current["sent_by_payload"].items():
            delta = max(0, amount - prior.get("sent_by_payload", {}).get(payload, 0))
            if delta:
                sent_by_payload[payload] = sent_by_payload.get(payload, 0) + delta
        for payload, amount in current["received_by_payload"].items():
            delta = max(0, amount - prior.get("received_by_payload", {}).get(payload, 0))
            if delta:
                received_by_payload[payload] = received_by_payload.get(payload, 0) + delta
    row["coordinator_node_wire_bytes_sent"] = sum(sent_by_node.values())
    row["coordinator_node_wire_bytes_received"] = sum(received_by_node.values())
    row["coordinator_node_wire_bytes_sent_by_payload"] = json.dumps(
        sent_by_payload, sort_keys=True
    )
    row["coordinator_node_wire_bytes_received_by_payload"] = json.dumps(
        received_by_payload, sort_keys=True
    )
    row["coordinator_node_wire_bytes_sent_by_node"] = json.dumps(sent_by_node, sort_keys=True)
    row["coordinator_node_wire_bytes_received_by_node"] = json.dumps(
        received_by_node, sort_keys=True
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("DSM_COORDINATOR_HOST", "localhost"))
    parser.add_argument("--server-name", default=os.getenv("DSM_TLS_SERVER_NAME"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DSM_COORDINATOR_PORT", "8443")))
    parser.add_argument("--label", default="benchmark-client")
    parser.add_argument("--ca-cert", default=os.getenv("DSM_CA_CERT_FILE"))
    parser.add_argument("--client-cert", default=os.getenv("DSM_CLIENT_CERT_FILE"))
    parser.add_argument("--client-key", default=os.getenv("DSM_CLIENT_KEY_FILE"))
    parser.add_argument("--sizes", type=int, nargs="+", default=[4096, 65536])
    parser.add_argument("--max-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--application-repeats", type=int, default=3,
                        help="repeated correctness-checked image and matrix runs per mode")
    parser.add_argument("--image-size", type=int, default=64)
    parser.add_argument("--tile-size", type=int, default=16)
    parser.add_argument("--block-size", type=int, default=3)
    parser.add_argument("--cpu-threads", type=int, default=1,
                        help="OpenCV thread limit (also set BLAS thread env vars before launch)")
    parser.add_argument("--worker-slots", type=int, default=int(os.getenv("NODE_COMPUTE_SLOTS", "1")),
                        help="recorded configured worker slots per resource node")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--scheduler-policy", default=os.getenv("DSM_COMPUTE_SCHEDULER", "locality"),
                        choices=("locality", "round_robin"))
    parser.add_argument("--distributed-compute", action="store_true",
                        help="run image and matrix operations on registered node workers")
    parser.add_argument("--input-access-mode", choices=("direct", "mediated"), default="direct",
                        help="select authorized local RAM reads or coordinator-mediated reads")
    parser.add_argument("--node-log", action="append", default=[])
    parser.add_argument("--node-stats-json", type=Path)
    parser.add_argument("--audit-jsonl", type=Path)
    parser.add_argument("--sample-process", action="append", default=[], metavar="ROLE=PID",
                        help="sample a coordinator or worker process for RSS (repeatable)")
    parser.add_argument("--memory-sample-interval", type=float, default=0.05)
    parser.add_argument("--output", type=Path, default=Path("benchmark-results.json"))
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    args = parser.parse_args()
    if (min(args.sizes) <= 0 or args.max_bytes <= 0 or args.repeats <= 0
            or args.cpu_threads <= 0
            or args.worker_slots <= 0
            or args.application_repeats <= 0 or args.memory_sample_interval <= 0):
        parser.error("sizes, max bytes, and repeat counts must be positive")
    try:
        for item in args.sample_process:
            role, pid = item.split("=", 1)
            if not role or int(pid) <= 0:
                raise ValueError
    except ValueError:
        parser.error("--sample-process must be ROLE=positive_pid")
    try:
        rows = asyncio.run(run(args))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.format == "json":
            args.output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        else:
            columns = sorted({key for row in rows for key in row})
            with args.output.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
        print(f"Wrote {len(rows)} authenticated DSM benchmark rows to {args.output}")
        return 0
    except Exception as exc:
        parser.exit(1, f"benchmark failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
