"""Benchmark file-backed matrix workloads against an authenticated DSM cluster."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from application.matrix_multiplication.matrix_adapter import MatrixMultiplicationAdapter
from compute import NetworkComputeAPI
from network.tcp.dsmapi_stub import RemoteDSMAPI
from scripts.benchmark_remote import ProcessMemorySampler, _cluster_traffic_snapshot


def _compare_rows(a: np.memmap, b: np.memmap, actual: np.memmap, rows: int = 32) -> None:
    for start in range(0, a.shape[0], rows):
        expected = np.asarray(a[start:start + rows]) @ np.asarray(b)
        np.testing.assert_allclose(actual[start:start + rows], expected, rtol=2e-4, atol=2e-4)


async def _native(a: np.memmap, b: np.memmap, output: Path, rows: int = 32) -> None:
    out = np.lib.format.open_memmap(output, mode="w+", dtype=a.dtype, shape=(a.shape[0], b.shape[1]))
    for start in range(0, a.shape[0], rows):
        out[start:start + rows] = np.asarray(a[start:start + rows]) @ np.asarray(b)
    out.flush()
    del out


async def run(args) -> list[dict]:
    secret = os.getenv("DSM_CLIENT_ENROLLMENT_SECRET")
    if not secret:
        raise ValueError("DSM_CLIENT_ENROLLMENT_SECRET is required")
    api = RemoteDSMAPI(args.host, args.port, "matrix-memmap-benchmark", secret,
                       server_hostname=args.server_name or args.host)
    sampler = ProcessMemorySampler(dict(item.split("=", 1) for item in args.sample_process), 0.05)
    await sampler.start()
    direct_api = NetworkComputeAPI(api, max_pending=args.max_pending, poll_interval=0.05,
                                   direct_local_reads=args.input_access_mode == "direct")
    rows: list[dict] = []
    try:
        await api.connect()
        for dimension in args.dimensions:
            input_dir = args.work_dir / f"matrix-{dimension}"
            input_dir.mkdir(parents=True, exist_ok=True)
            a_path, b_path = input_dir / "a.npy", input_dir / "b.npy"
            a = MatrixMultiplicationAdapter.generate_memmap_matrix(
                a_path, (dimension, dimension), args.seed, np.float32, args.generation_rows
            )
            b = MatrixMultiplicationAdapter.generate_memmap_matrix(
                b_path, (dimension, dimension), args.seed + 1, np.float32, args.generation_rows
            )
            for block_size in args.block_sizes:
                for mode in args.modes:
                    samples, stage_samples = [], {"input_prepare_s": [], "execution_s": [], "output_collect_s": []}
                    for repetition in range(args.repeats):
                        output_path = args.work_dir / f"c-{dimension}-{block_size}-{mode}-{repetition}.npy"
                        before_task_ids = set(direct_api._results)
                        if mode == "native":
                            started = perf_counter()
                            await _native(a, b, output_path, args.generation_rows)
                            samples.append(perf_counter() - started)
                        else:
                            compute_api = direct_api if mode == "distributed_compute" else None
                            adapter = MatrixMultiplicationAdapter(api, block_size, compute_api)
                            cluster_before = await _cluster_traffic_snapshot(api)
                            network_before = api.connection.stats()
                            started = perf_counter()
                            inputs = await adapter.prepare_from_memmaps(a_path, b_path)
                            stage_samples["input_prepare_s"].append(perf_counter() - started)
                            started = perf_counter()
                            outputs = await adapter.execute(inputs)
                            stage_samples["execution_s"].append(perf_counter() - started)
                            started = perf_counter()
                            actual = await adapter.collect_result_to_memmap(outputs, output_path)
                            stage_samples["output_collect_s"].append(perf_counter() - started)
                            del actual
                            await adapter.cleanup(inputs, outputs)
                            samples.append(sum(stage_samples[key][-1] for key in stage_samples))
                        actual = np.load(output_path, mmap_mode="r", allow_pickle=False)
                        _compare_rows(a, b, actual)
                        row = {
                            "dimension": dimension, "dtype": "float32", "block_size": block_size,
                            "mode": mode, "input_access_mode": args.input_access_mode,
                            "repetition": repetition, "end_to_end_s": samples[-1],
                            "numerically_correct": True,
                        }
                        if mode == "distributed_compute":
                            task_results = [result for task_id, result in direct_api._results.items()
                                            if task_id not in before_task_ids]
                            row["worker_task_count"] = len(task_results)
                            row["worker_node_ids"] = sorted({r.node_id for r in task_results})
                            row["local_read_bytes"] = sum(r.metrics.get("local_read_bytes", 0)
                                                           for r in task_results)
                            row["remote_read_bytes"] = sum(r.metrics.get("remote_read_bytes", 0)
                                                            for r in task_results)
                        row.update({key: values[-1] for key, values in stage_samples.items() if values})
                        row["client_wire_bytes_sent"] = api.connection.stats()["wire_bytes_sent"] - network_before["wire_bytes_sent"] if mode != "native" else 0
                        row["client_wire_bytes_received"] = api.connection.stats()["wire_bytes_received"] - network_before["wire_bytes_received"] if mode != "native" else 0
                        if mode != "native":
                            cluster_after = await _cluster_traffic_snapshot(api)
                            row["coordinator_node_wire_bytes_sent"] = sum(
                                max(0, val["sent"] - cluster_before.get(node, {}).get("sent", 0))
                                for node, val in cluster_after.items()
                            )
                            row["coordinator_node_wire_bytes_received"] = sum(
                                max(0, val["received"] - cluster_before.get(node, {}).get("received", 0))
                                for node, val in cluster_after.items()
                            )
                        rows.append(row)
                        del actual
            del a, b
        memory = await sampler.stop()
        blas_threads = {key: os.getenv(key) for key in
                        ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}
        for row in rows:
            row.update(memory)
            row["hardware"] = {
                "platform": platform.platform(), "processor": platform.processor(),
                "logical_cpu_count": os.cpu_count(), "blas_thread_environment": blas_threads,
                "requested_cpu_thread_limit": args.cpu_threads,
                "worker_slots_per_node": args.worker_slots,
            }
        return rows
    finally:
        await sampler.stop()
        await api.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("DSM_COORDINATOR_HOST", "localhost"))
    parser.add_argument("--server-name", default=os.getenv("DSM_TLS_SERVER_NAME"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DSM_COORDINATOR_PORT", "8443")))
    parser.add_argument("--work-dir", type=Path, default=Path(".matrix-benchmark"))
    parser.add_argument("--dimensions", type=int, nargs="+", default=[256, 512, 1024])
    parser.add_argument("--block-sizes", type=int, nargs="+", default=[256, 512])
    parser.add_argument("--modes", nargs="+", choices=("native", "local_tiled", "distributed_compute"),
                        default=["native", "local_tiled", "distributed_compute"])
    parser.add_argument("--input-access-mode", choices=("direct", "mediated"), default="direct")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--generation-rows", type=int, default=32)
    parser.add_argument("--seed", type=int, default=817)
    parser.add_argument("--max-pending", type=int, default=4)
    parser.add_argument("--cpu-threads", type=int, default=1,
                        help="record desired limit; set BLAS thread env vars before launching Python")
    parser.add_argument("--worker-slots", type=int, default=1)
    parser.add_argument("--sample-process", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("matrix-memmap-results.json"))
    args = parser.parse_args()
    if min(args.dimensions + args.block_sizes + [args.repeats, args.generation_rows,
                                                  args.max_pending, args.cpu_threads,
                                                  args.worker_slots]) <= 0:
        parser.error("dimensions, tile sizes, repeats, and resource limits must be positive")
    rows = asyncio.run(run(args))
    for dimension, block_size, mode in sorted({
        (row["dimension"], row["block_size"], row["mode"]) for row in rows
    }):
        values = [row["end_to_end_s"] for row in rows if
                  (row["dimension"], row["block_size"], row["mode"]) ==
                  (dimension, block_size, mode)]
        summary = {"dimension": dimension, "block_size": block_size, "mode": mode,
                   "median_s": statistics.median(values), "min_s": min(values), "max_s": max(values)}
        rows.append(summary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} matrix experiment rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
