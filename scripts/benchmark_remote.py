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
from statistics import mean
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from application.checksum import ChecksumAdapter
from application.image_processing.image_adapter import ImageProcessingAdapter, apply_operation
from application.matrix_multiplication.matrix_adapter import MatrixMultiplicationAdapter
from network.tcp.dsmapi_stub import RemoteDSMAPI


async def run(args: argparse.Namespace) -> list[dict]:
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
    audit_before = Path(args.audit_jsonl).stat().st_size if args.audit_jsonl and Path(args.audit_jsonl).exists() else 0
    try:
        await asyncio.wait_for(api.connect(), timeout=15)
        for size in args.sizes:
            if size > args.max_bytes:
                raise ValueError(f"size {size} exceeds --max-bytes {args.max_bytes}")
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
            rows.append({"workload": "remote_read_write", "size_bytes": size,
                         "repeat_count": args.repeats, "read_latency_mean_s": mean(reads),
                         "write_latency_mean_s": mean(writes),
                         "read_throughput_bytes_s": size / mean(reads),
                         "write_throughput_bytes_s": size / mean(writes),
                         "scheduler_policy": args.scheduler_policy,
                         "security_path": "TLS+authenticated token+audit"})
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
            adapter = ImageProcessingAdapter(api, operation=operation,
                                             tile_height=args.tile_size,
                                             tile_width=args.tile_size,
                                             halo=2 if operation == "blur" else 0)
            started = perf_counter()
            output = await adapter.run(image)
            correct = np.array_equal(output, apply_operation(image, operation))
            if not correct:
                raise AssertionError(f"{operation} image output mismatch")
            rows.append({"workload": f"image_{operation}", "end_to_end_s": perf_counter() - started,
                         "correct": correct, "scheduler_policy": args.scheduler_policy,
                         "security_path": "TLS+authenticated token+audit"})

        a, b = rng.standard_normal((7, 5)), rng.standard_normal((5, 6))
        adapter = MatrixMultiplicationAdapter(api, block_size=args.block_size)
        started = perf_counter()
        product = await adapter.run((a, b))
        correct = np.allclose(product, a @ b, rtol=1e-10, atol=1e-10)
        if not correct:
            raise AssertionError("matrix benchmark output mismatch")
        rows.append({"workload": "matrix_multiply", "end_to_end_s": perf_counter() - started,
                     "correct": bool(correct), "scheduler_policy": args.scheduler_policy,
                     "security_path": "TLS+authenticated token+audit"})
    finally:
        for object_id in reversed(allocated):
            await api.free(object_id)
        await api.close()

    node_logs = {str(path): Path(path).read_text(encoding="utf-8", errors="replace")
                 for path in args.node_log}
    placements = {name: sum(f"write {object_id}" in log for object_id in allocated_all)
                  for name, log in node_logs.items()}
    for row in rows:
        row["per_node_allocation_evidence"] = json.dumps(placements, sort_keys=True)
        row["nodes_with_allocations"] = sum(count > 0 for count in placements.values())
    if args.node_stats_json:
        stats = json.loads(Path(args.node_stats_json).read_text(encoding="utf-8"))
        for row in rows:
            row["per_node_stats_json"] = json.dumps(stats, sort_keys=True)
    if args.audit_jsonl and Path(args.audit_jsonl).exists():
        after = Path(args.audit_jsonl).stat().st_size
        for row in rows:
            row["audit_bytes_added"] = after - audit_before
    return rows


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
    parser.add_argument("--image-size", type=int, default=64)
    parser.add_argument("--tile-size", type=int, default=16)
    parser.add_argument("--block-size", type=int, default=3)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--scheduler-policy", default="configured-on-coordinator")
    parser.add_argument("--node-log", action="append", default=[])
    parser.add_argument("--node-stats-json", type=Path)
    parser.add_argument("--audit-jsonl", type=Path)
    parser.add_argument("--output", type=Path, default=Path("benchmark-results.json"))
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    args = parser.parse_args()
    if min(args.sizes) <= 0 or args.max_bytes <= 0 or args.repeats <= 0:
        parser.error("sizes, max bytes, and repeats must be positive")
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
