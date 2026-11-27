"""Reproducible float32 matrix benchmark; DSM modes use MatrixMultiplicationAdapter."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
import threading
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np


class RssPeakSampler:
    def __init__(self, interval: float = 0.05):
        self.interval = interval
        self.peak = 0
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        try:
            import psutil
        except ImportError:
            return self
        process = psutil.Process()
        def sample():
            while not self.stop_event.is_set():
                try:
                    self.peak = max(self.peak, process.memory_info().rss)
                except psutil.Error:
                    pass
                self.stop_event.wait(self.interval)
        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.thread:
            self.stop_event.set()
            self.thread.join()
        return self.peak or _peak_rss()


def generate(path: Path, n: int, seed: int, rows: int, tile: int) -> np.memmap:
    out = np.lib.format.open_memmap(path, mode="w+", dtype=np.float32, shape=(n, n))
    rng = np.random.default_rng(seed)
    for r in range(0, n, rows):
        out[r:r + rows] = rng.standard_normal((min(rows, n-r), n), dtype=np.float32)
    out.flush()
    return out


def local_tiled(a, b, output: Path, tile: int) -> float:
    n = a.shape[0]
    c = np.lib.format.open_memmap(output, mode="w+", dtype=np.float32, shape=(n, n))
    c[:] = 0
    started = perf_counter()
    for i in range(0, n, tile):
        for k in range(0, n, tile):
            ab = np.asarray(a[i:i+tile, k:k+tile])
            for j in range(0, n, tile):
                c[i:i+tile, j:j+tile] += ab @ np.asarray(b[k:k+tile, j:j+tile])
    c.flush()
    elapsed = perf_counter() - started
    del c
    return elapsed


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dimensions", type=int, nargs="+", default=[512, 1024, 2048])
    p.add_argument("--tile", type=int, choices=(128, 256, 512, 1024, 2048), default=256)
    p.add_argument("--mode", choices=("native", "local_tiled", "client_compute", "mediated", "direct", "optimized"), default="local_tiled")
    p.add_argument("--seed", type=int, default=817)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--generation-rows", type=int, default=32)
    p.add_argument("--work-dir", type=Path, default=Path(".final-matrix-benchmark"))
    p.add_argument("--output", type=Path, default=Path("results/final-matrix.json"))
    p.add_argument("--max-storage-gib", type=float, default=64)
    p.add_argument("--max-working-set-gib", type=float, default=64)
    p.add_argument("--max-pending", type=int, default=4)
    p.add_argument("--io-concurrency", type=int, default=1)
    p.add_argument("--require-workers", type=int, default=1)
    p.add_argument("--poll-interval", type=float, default=0.05)
    p.add_argument("--input-access", choices=("direct", "mediated"), default="direct")
    p.add_argument("--scheduler", choices=("locality", "round_robin"), default="locality")
    a = p.parse_args()
    if min(a.dimensions + [a.tile, a.repeats, a.generation_rows, a.max_pending, a.io_concurrency,
                           a.require_workers]) <= 0 or min(a.max_storage_gib, a.max_working_set_gib) <= 0:
        p.error("dimensions, tile, repeats, and generation rows must be positive")
    a.work_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for n in a.dimensions:
        rss_sampler = RssPeakSampler().start()
        estimate = 3 * n * n * 4
        working_estimate = estimate + 4 * n**3 // a.tile
        if working_estimate > a.max_storage_gib * 1024**3 or working_estimate > a.max_working_set_gib * 1024**3:
            p.error(f"{n} needs an estimated {working_estimate / 1024**3:.2f} GiB including partial tiles; exceeds configured limit")
        preflight = None
        if a.mode not in ("native", "local_tiled"):
            preflight = asyncio.run(_preflight(a, n, working_estimate))
        samples = []
        for rep in range(a.repeats):
            ap, bp = a.work_dir / f"a-{n}-{rep}.npy", a.work_dir / f"b-{n}-{rep}.npy"
            t0 = perf_counter()
            aa = generate(ap, n, a.seed, a.generation_rows, a.tile)
            bb = generate(bp, n, a.seed + 1, a.generation_rows, a.tile)
            input_prepare = perf_counter() - t0
            cp = a.work_dir / f"c-{n}-{rep}.npy"
            mode_started = perf_counter()
            if a.mode == "native":
                # Native is permitted only when the two inputs and output fit as RAM arrays.
                if estimate > 0.7 * _available_ram():
                    p.error("native mode exceeds 70% of available RAM; use local_tiled")
                np.save(cp, np.asarray(aa) @ np.asarray(bb))
                compute = perf_counter() - mode_started
            elif a.mode == "local_tiled":
                compute = local_tiled(aa, bb, cp, a.tile)
            else:
                from application.matrix_multiplication.matrix_adapter import MatrixMultiplicationAdapter
                from compute import NetworkComputeAPI
                from network.tcp.dsmapi_stub import RemoteDSMAPI
                secret = os.getenv("DSM_CLIENT_ENROLLMENT_SECRET")
                if not secret: p.error("distributed_compute requires DSM_CLIENT_ENROLLMENT_SECRET")
                async def run_remote():
                    api = RemoteDSMAPI(os.getenv("DSM_COORDINATOR_HOST", "localhost"), int(os.getenv("DSM_COORDINATOR_PORT", "8443")), "final-matrix", secret, server_hostname=os.getenv("DSM_TLS_SERVER_NAME", "localhost"))
                    stages = {}
                    telemetry = {}
                    phase = perf_counter()
                    await api.connect()
                    stages["connect_s"] = perf_counter() - phase
                    wire_before = api.connection.stats()
                    cluster_before = await api.get_cluster_stats()
                    is_worker = a.mode in ("mediated", "direct", "optimized")
                    direct = a.mode in ("direct", "optimized") and a.input_access == "direct"
                    compute_api = NetworkComputeAPI(api, max_pending=a.max_pending, poll_interval=a.poll_interval,
                                                    direct_local_reads=direct) if is_worker else None
                    adapter = MatrixMultiplicationAdapter(api, a.tile, compute_api, io_concurrency=a.io_concurrency)
                    try:
                        phase = perf_counter()
                        w = await adapter.prepare_from_memmaps(ap, bp)
                        stages["input_prepare_alloc_transfer_seal_s"] = perf_counter() - phase
                        stages["adapter_chunking_s"] = adapter.timings.get("chunking", 0.0)
                        phase = perf_counter()
                        out = await adapter.execute(w)
                        stages["schedule_and_compute_wall_s"] = perf_counter() - phase
                        stages["worker_compute_sum_s"] = adapter.timings.get("processing", 0.0)
                        new_results = list(compute_api._results.values()) if compute_api else []
                        telemetry["worker_task_count"] = len(new_results)
                        telemetry["worker_node_ids"] = sorted({result.node_id for result in new_results})
                        metrics = [result.metrics for result in new_results]
                        for key in ("local_read_count", "local_read_bytes", "remote_read_count",
                                    "remote_read_bytes", "cache_hit_count", "cache_miss_count",
                                    "cache_bytes_avoided", "dsm_requests"):
                            telemetry[key] = sum(float(m.get(key, 0)) for m in metrics)
                        telemetry["worker_execution_sum_s"] = sum(
                            max(0.0, result.execution_finished_at - result.execution_started_at)
                            for result in new_results
                        )
                        local_bytes = telemetry.get("local_read_bytes", 0.0)
                        remote_bytes = telemetry.get("remote_read_bytes", 0.0)
                        telemetry["locality_ratio"] = local_bytes / max(1.0, local_bytes + remote_bytes)
                        phase = perf_counter()
                        await adapter.collect_result_to_memmap(out, cp)
                        stages["result_collection_s"] = perf_counter() - phase
                        phase = perf_counter()
                        await adapter.cleanup(w, out)
                        stages["cleanup_s"] = perf_counter() - phase
                        wire_after = api.connection.stats()
                        for key in ("wire_bytes_sent", "wire_bytes_received", "requests_sent", "responses_received"):
                            telemetry[f"client_{key}"] = wire_after[key] - wire_before[key]
                        cluster_after = await api.get_cluster_stats()
                        before_nodes = {node.node_id: node for node in cluster_before}
                        node_wire = {}
                        for node in cluster_after:
                            prior = before_nodes.get(node.node_id)
                            if prior:
                                delta = int(node.coordinator_wire_bytes_sent - prior.coordinator_wire_bytes_sent)
                                delta += int(node.coordinator_wire_bytes_received - prior.coordinator_wire_bytes_received)
                                node_wire[node.node_id] = max(0, delta)
                        telemetry["coordinator_node_wire_bytes"] = sum(node_wire.values())
                        telemetry["node_wire_bytes"] = node_wire
                        telemetry["node_resources"] = {
                            node.node_id: {
                                "used_memory_bytes_before": int(before_nodes[node.node_id].used_memory),
                                "used_memory_bytes_after": int(node.used_memory),
                                "cpu_percent": float(node.cpu_percent),
                                "available_memory_bytes": int(node.available_memory),
                                "wire_bytes_delta": node_wire.get(node.node_id, 0),
                            }
                            for node in cluster_after if node.node_id in before_nodes
                        }
                        telemetry["aggregate_node_used_bytes_before"] = sum(
                            int(node.used_memory) for node in cluster_before)
                        telemetry["aggregate_node_used_bytes_after"] = sum(
                            int(node.used_memory) for node in cluster_after)
                        telemetry["aggregate_node_cpu_percent"] = sum(
                            float(node.cpu_percent) for node in cluster_after) / max(1, len(cluster_after))
                    finally: await api.close()
                    return stages, telemetry
                stages, telemetry = asyncio.run(run_remote())
                compute = stages.get("schedule_and_compute_wall_s", 0.0)
            elapsed = perf_counter() - t0
            got = np.load(cp, mmap_mode="r", allow_pickle=False)
            rng = np.random.default_rng(a.seed + rep + 42)
            for _ in range(min(8, n)):
                i, j = int(rng.integers(n)), int(rng.integers(n))
                expected = np.dot(np.asarray(aa[i], dtype=np.float64), np.asarray(bb[:, j], dtype=np.float64))
                if not np.isclose(got[i, j], expected, rtol=2e-4, atol=2e-4): raise AssertionError(f"sample mismatch at {(i,j)}")
            samples.append({"repetition": rep, "end_to_end_s": elapsed, "compute_s": compute,
                            "input_prepare_s": input_prepare, "sampled_correct": True,
                            **(stages if a.mode not in ("native", "local_tiled") else {}),
                            **(telemetry if a.mode not in ("native", "local_tiled") else {})})
        rows.append({"dimension": n, "tile": a.tile, "mode": a.mode, "dtype": "float32", "seed": a.seed,
                     "minimum_storage_bytes": estimate, "estimated_working_set_bytes": working_estimate,
                     "preflight": preflight, "median_s": statistics.median(x["end_to_end_s"] for x in samples),
                     "variability_s": max(x["end_to_end_s"] for x in samples)-min(x["end_to_end_s"] for x in samples),
                     "peak_rss_bytes": rss_sampler.stop(),
                     "scheduler": a.scheduler, "requested_input_access": a.input_access,
                     "max_pending": a.max_pending, "poll_interval_s": a.poll_interval,
                     "io_concurrency": a.io_concurrency,
                     "samples": samples, "verification": "sampled output cells"})
        del aa, bb
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    csv_path = a.output.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[k for k in rows[0] if k != "samples"]); w.writeheader(); w.writerows([{k:v for k,v in r.items() if k != "samples"} for r in rows])
    print(f"Wrote {a.output} and {csv_path}")
    return 0


def _available_ram() -> int:
    try:
        import psutil
        return psutil.virtual_memory().available
    except ImportError:
        return 2 * 1024**3


def _peak_rss() -> int | None:
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except ImportError:
        return None


async def _preflight(args, n: int, working_estimate: int) -> dict:
    from network.tcp.dsmapi_stub import RemoteDSMAPI
    secret = os.getenv("DSM_CLIENT_ENROLLMENT_SECRET")
    if not secret:
        raise ValueError("distributed benchmark requires DSM_CLIENT_ENROLLMENT_SECRET")
    api = RemoteDSMAPI(os.getenv("DSM_COORDINATOR_HOST", "localhost"),
                       int(os.getenv("DSM_COORDINATOR_PORT", "8443")), "matrix-preflight", secret,
                       server_hostname=os.getenv("DSM_TLS_SERVER_NAME", "localhost"))
    try:
        await api.connect()
        nodes = await api.get_cluster_stats()
        workers = [node for node in nodes if node.compute_slots and node.compute_operations]
        if len(workers) < args.require_workers:
            raise ValueError(f"preflight found {len(workers)} compute workers; requires {args.require_workers}")
        available = sum(max(0, int(node.total_memory) - int(node.used_memory)) for node in nodes)
        if available < working_estimate:
            raise ValueError(f"preflight needs {working_estimate} bytes; cluster reports {available} free")
        max_node = max((int(node.total_memory) for node in nodes), default=0)
        tile_bytes = min(args.tile, n) ** 2 * 4
        if max_node < tile_bytes:
            raise ValueError(f"largest node capacity {max_node} is below one input tile ({tile_bytes})")
        return {"registered_nodes": len(nodes), "compute_workers": len(workers),
                "reported_free_bytes": available, "largest_node_bytes": max_node,
                "required_working_set_bytes": working_estimate}
    finally:
        await api.close()


if __name__ == "__main__": raise SystemExit(main())
