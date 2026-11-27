"""Remote compute smoke test using separate coordinator and node processes."""
import asyncio
import json
import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psutil
import pytest
import yaml
from cryptography.fernet import Fernet

from application.image_processing.image_adapter import ImageProcessingAdapter, apply_operation
from application.matrix_multiplication.matrix_adapter import MatrixMultiplicationAdapter
from compute import NetworkComputeAPI
from network.tcp.dsmapi_stub import RemoteDSMAPI


ROOT = Path(__file__).resolve().parents[3]
PYTHON = sys.executable
pytestmark = [pytest.mark.integration, pytest.mark.full_stack]


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for(predicate, description, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if await value if asyncio.iscoroutine(value) else value:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {description}")


@pytest.mark.asyncio
async def test_remote_image_and_matrix_compute(tmp_path, monkeypatch):
    cert_cwd = tmp_path / "cert-work"
    cert_cwd.mkdir()
    subprocess.run([PYTHON, str(ROOT / "scripts/gen_certs.py")], cwd=cert_cwd,
                   check=True, capture_output=True, text=True, timeout=30)
    cert_dir = cert_cwd / "certs"
    subprocess.run([PYTHON, str(ROOT / "scripts/gen_certs.py"), "--issue-node", "node-2"],
                   cwd=cert_cwd, check=True, capture_output=True, text=True, timeout=30)
    subprocess.run([PYTHON, str(ROOT / "scripts/gen_certs.py"), "--issue-node", "node-3"],
                   cwd=cert_cwd, check=True, capture_output=True, text=True, timeout=30)
    monkeypatch.setenv("DSM_CERT_DIR", str(cert_dir))

    secret = "remote-compute-integration-secret-32-bytes"
    security = yaml.safe_load((ROOT / "configs/security.yaml").read_text())
    security["token"]["ttl_seconds"] = 600
    security["rate_limit"]["node_level"]["max_memory_fraction_per_client"] = 0.9
    security["persistence"]["database_path"] = str(tmp_path / "security.db")
    security["audit"]["path"] = str(tmp_path / "audit.jsonl")
    security["enrollment"]["denylist_path"] = str(tmp_path / "denylist.json")
    security_path = tmp_path / "security.yaml"
    security_path.write_text(yaml.safe_dump(security))

    port = _free_port()
    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "DSM_CERT_DIR": str(cert_dir),
        "DSM_SECURITY_CONFIG": str(security_path),
        "DSM_NODE_ENROLLMENT_SECRET": secret,
        "DSM_CLIENT_ENROLLMENT_SECRET": secret,
        "DSM_TOKEN_KEY": Fernet.generate_key().decode("ascii"),
        "DSM_COMPUTE_SCHEDULER": "locality",
        "SECURITY_HOST": "127.0.0.1",
        "SECURITY_PORT": str(port),
        "NODE_ID": "remote-compute-node-1",
        "DSM_NODE_CERT_FILE": str(cert_dir / "nodes/node-1.pem"),
        "DSM_NODE_KEY_FILE": str(cert_dir / "nodes/node-1.key"),
        "NODE_CAPACITY_BYTES": str(10 * 1024 * 1024),
        "NODE_COMPUTE_SLOTS": "2",
        "NODE_HEADROOM_FRACTION": "0.01",
        "NODE_HEADROOM_MIN_BYTES": "0",
        "NODE_HEARTBEAT_INTERVAL": "1",
        "NODE_CPU_THREADS": "1",
        "NODE_COMPUTE_MEMORY_BYTES": str(16 * 1024 * 1024),
    })
    coordinator_log = (tmp_path / "coordinator.log").open("w+")
    node_logs = []
    coordinator = subprocess.Popen(
        [PYTHON, str(ROOT / "scripts/coordinator_main.py")], cwd=ROOT,
        env=env, stdout=coordinator_log, stderr=subprocess.STDOUT,
    )
    nodes = []
    client = None
    try:
        async def coordinator_ready():
            try:
                _, writer = await asyncio.open_connection(
                    "127.0.0.1", port,
                    ssl=ssl.create_default_context(cafile=str(cert_dir / "ca.pem")),
                    server_hostname="localhost",
                )
                writer.close()
                await writer.wait_closed()
                return True
            except OSError:
                return False

        await _wait_for(coordinator_ready, "coordinator TLS readiness")
        for node_index, cert_id in enumerate(("node-1", "node-2", "node-3"), 1):
            node_env = env.copy()
            node_env["NODE_ID"] = f"remote-compute-{cert_id}"
            node_env["DSM_NODE_CERT_FILE"] = str(cert_dir / f"nodes/{cert_id}.pem")
            node_env["DSM_NODE_KEY_FILE"] = str(cert_dir / f"nodes/{cert_id}.key")
            log = (tmp_path / f"node-{node_index}.log").open("w+")
            node_logs.append(log)
            nodes.append(subprocess.Popen(
                [PYTHON, str(ROOT / "scripts/node_main.py")], cwd=ROOT,
                env=node_env, stdout=log, stderr=subprocess.STDOUT,
            ))
        await _wait_for(
            lambda: all(
                "[node] registered as " in (tmp_path / f"node-{i}.log").read_text(errors="replace")
                for i in range(1, 3)
            ),
            "both compute node registrations",
        )
        client = RemoteDSMAPI("127.0.0.1", port, "remote-compute-client", secret)
        await asyncio.wait_for(client.connect(), timeout=10)
        compute = NetworkComputeAPI(client, max_pending=4, poll_interval=0.05)

        rng = np.random.default_rng(2026)
        image = rng.integers(0, 256, (33, 35, 3), dtype=np.uint8)
        image_task_results = []
        for operation in ("invert", "grayscale", "blur"):
            previous_ids = set(compute._results)
            image_result = await asyncio.wait_for(
                ImageProcessingAdapter(
                    client, operation=operation, tile_height=16, tile_width=16,
                    halo=2 if operation == "blur" else 0, compute_api=compute,
                ).run(image), timeout=60,
            )
            np.testing.assert_array_equal(image_result, apply_operation(image, operation))
            operation_tasks = [
                result for task_id, result in compute._results.items()
                if task_id not in previous_ids
            ]
            assert len(operation_tasks) == 9
            assert all(result.status.value == "COMPLETED" for result in operation_tasks)
            assert all(result.worker_pid != os.getpid() for result in operation_tasks)
            assert all(result.operation == f"image.{operation}" for result in operation_tasks)
            image_task_results.extend(operation_tasks)

        matrix_a = rng.standard_normal((5, 7))
        matrix_b = rng.standard_normal((7, 6))
        previous_ids = set(compute._results)
        matrix_result = await asyncio.wait_for(
            MatrixMultiplicationAdapter(client, block_size=3, compute_api=compute).run(
                (matrix_a, matrix_b)
            ), timeout=90,
        )
        np.testing.assert_allclose(matrix_result, matrix_a @ matrix_b)
        matrix_tasks = [
            result for task_id, result in compute._results.items() if task_id not in previous_ids
        ]
        assert sum(r.operation == "matrix.multiply_block" for r in matrix_tasks) == 12
        assert sum(r.operation == "matrix.add_block" for r in matrix_tasks) >= 4
        assert all(result.status.value == "COMPLETED" for result in matrix_tasks)
        assert all(result.worker_pid != os.getpid() for result in matrix_tasks)
        assert all(result.metrics.get("pin_operation_count", 0) >= 1
                   and result.metrics.get("release_operation_count", 0)
                   == result.metrics.get("pin_operation_count", 0)
                   for result in (*image_task_results, *matrix_tasks))
        assert all(result.metrics.get("seal_operation_count", 0) == 1
                   for result in (*image_task_results, *matrix_tasks))
        assert all(result.metrics.get("local_output_write_count", 0)
                   + result.metrics.get("remote_output_write_count", 0) == 1
                   for result in (*image_task_results, *matrix_tasks))
        worker_nodes = {r.node_id for r in (*image_task_results, *matrix_tasks)}
        expected_worker_nodes = {
            (tmp_path / f"node-{index}.log").read_text(errors="replace")
            .split("[node] registered as ", 1)[1].splitlines()[0]
            for index in range(1, 4)
        }
        assert worker_nodes == expected_worker_nodes, (
            "tasks did not execute on all three resource-node processes, including the client host node"
        )
        remote_results = (*image_task_results, *matrix_tasks)
        assert all(len(r.output_object_ids) == 1 for r in remote_results)
        assert sum(r.metrics.get("local_read_count", 0) for r in remote_results) > 0
        assert sum(r.metrics.get("local_read_bytes", 0) for r in remote_results) > 0
        output_ids = [oid for r in remote_results for oid in r.output_object_ids]
        assert len(output_ids) == len(set(output_ids)), "worker outputs were committed more than once"
        assert any(
            left.node_id != right.node_id
            and left.execution_started_at < right.execution_finished_at
            and right.execution_started_at < left.execution_finished_at
            for index, left in enumerate(remote_results)
            for right in remote_results[index + 1:]
        ), "no application tasks overlapped on independent workers"
        audit_events = [
            json.loads(line)
            for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert not any(
            event.get("actor_id") in worker_nodes
            and event.get("event_type") == "rate_limit_violation"
            for event in audit_events
        ), "normal compute traffic exhausted a worker identity's request budget"

        distributed_rows_by_access = {}
        for access_mode in ("mediated", "direct"):
            benchmark_output = tmp_path / f"benchmark-{access_mode}.json"
            benchmark_command = [PYTHON, str(ROOT / "scripts/benchmark_remote.py"),
                 "--host", "127.0.0.1", "--port", str(port), "--sizes", "128",
                 "--max-bytes", "1024", "--repeats", "1", "--application-repeats", "2",
                 "--image-size", "8", "--tile-size", "8", "--block-size", "8",
                 "--input-access-mode", access_mode,
                 "--cpu-threads", "1", "--worker-slots", "2",
                 "--sample-process", f"coordinator={coordinator.pid}"]
            for node_index, process in enumerate(nodes, 1):
                benchmark_command.extend(("--sample-process", f"node-{node_index}={process.pid}"))
            benchmark_command.extend(("--distributed-compute", "--format", "json",
                                      "--output", str(benchmark_output)))
            benchmark = subprocess.run(
                benchmark_command,
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=90,
            )
            assert benchmark.returncode == 0, benchmark.stderr
            benchmark_rows = json.loads(benchmark_output.read_text(encoding="utf-8"))
            application_rows = [row for row in benchmark_rows if row["workload"].startswith("image_")
                                or row["workload"] == "matrix_multiply"]
            assert {row["execution_mode"] for row in application_rows} == {
                "native", "local_tiled", "client_compute", "distributed_compute"
            }
            assert all(row["application_repeat_count"] == 2 for row in application_rows)
            assert all(row["input_access_mode"] == access_mode for row in application_rows)
            network_rows = [row for row in application_rows
                            if row["execution_mode"] in ("client_compute", "distributed_compute")]
            assert all(row["client_wire_bytes_sent"] > 0 for row in network_rows)
            assert all(row["client_requests_sent"] > 0 for row in network_rows)
            assert all("client" in row["peak_rss_bytes_by_process"] for row in application_rows)
            assert all("coordinator" in row["peak_rss_bytes_by_process"] for row in application_rows)
            assert all(all(f"node-{index}" in row["peak_rss_bytes_by_process"]
                           for index in range(1, 4)) for row in application_rows)
            distributed_rows = [row for row in application_rows
                                if row["execution_mode"] == "distributed_compute"]
            assert all(row["worker_task_count"] > 0 for row in distributed_rows)
            assert all(row["seal_operation_count"] == row["worker_task_count"]
                       for row in distributed_rows)
            assert all(row["pin_operation_count"] == row["release_operation_count"]
                       and row["pin_operation_count"] >= row["worker_task_count"]
                       for row in distributed_rows)
            assert all(row["local_output_write_count"] + row["remote_output_write_count"]
                       == row["worker_task_count"] for row in distributed_rows)
            assert all(row["end_to_end_median_s"] > 0 for row in application_rows)
            native_rows = [row for row in application_rows if row["execution_mode"] == "native"]
            assert all(row["timed_operation_calls"] == 2 for row in native_rows), (
                "native benchmark rows must execute the operation once for every timed repetition"
            )
            distributed_rows_by_access[access_mode] = distributed_rows

        mediated_rows = distributed_rows_by_access["mediated"]
        direct_rows = distributed_rows_by_access["direct"]
        assert all(row["local_read_bytes"] == 0 for row in mediated_rows)
        assert all(row["remote_read_bytes"] > 0 for row in mediated_rows)
        assert sum(row["local_read_bytes"] for row in direct_rows) > 0
        assert all(left["workload"] == right["workload"]
                   for left, right in zip(mediated_rows, direct_rows))

        # Capacity demonstration: the resident A+B+C storage exceeds any one
        # 10 MiB node while fitting within the three-node configured pool.
        dimension, block_size, itemsize = 1024, 512, np.dtype(np.float32).itemsize
        required_bytes = 3 * dimension * dimension * itemsize
        node_stats = await client.get_cluster_stats()
        assert len(node_stats) == 3
        configured_per_node = [int(node.total_memory) for node in node_stats]
        assert max(configured_per_node) < required_bytes <= sum(configured_per_node)

        capacity_dir = tmp_path / "capacity"
        capacity_dir.mkdir()
        a_path, b_path, c_path = (capacity_dir / "a.npy", capacity_dir / "b.npy",
                                  capacity_dir / "c.npy")
        role_pids = {"client": os.getpid(), "coordinator": coordinator.pid,
                     **{f"node-{i}": process.pid for i, process in enumerate(nodes, 1)}}
        baseline_rss = {role: psutil.Process(pid).memory_info().rss
                        for role, pid in role_pids.items()}
        peak_rss = dict(baseline_rss)
        aggregate_rss_peak = sum(baseline_rss.values())
        stop_rss = asyncio.Event()
        stop_storage = asyncio.Event()
        storage_peak = {}

        async def sample_rss():
            nonlocal aggregate_rss_peak
            while not stop_rss.is_set():
                snapshot = {}
                for role, pid in role_pids.items():
                    try:
                        snapshot[role] = psutil.Process(pid).memory_info().rss
                        peak_rss[role] = max(peak_rss[role], snapshot[role])
                    except psutil.Error:
                        pass
                aggregate_rss_peak = max(aggregate_rss_peak, sum(snapshot.values()))
                await asyncio.sleep(0.05)

        async def sample_storage():
            while not stop_storage.is_set():
                try:
                    for node in await client.get_cluster_stats():
                        storage_peak[node.node_id] = max(
                            storage_peak.get(node.node_id, 0), int(node.used_memory)
                        )
                except Exception:
                    pass
                await asyncio.sleep(0.25)

        rss_task = asyncio.create_task(sample_rss())
        storage_task = asyncio.create_task(sample_storage())
        capacity_adapter = MatrixMultiplicationAdapter(client, block_size=block_size,
                                                        compute_api=compute)
        input_workload = output_workload = None
        a_map = b_map = None
        try:
            a_map = MatrixMultiplicationAdapter.generate_memmap_matrix(
                a_path, (dimension, dimension), seed=3001, dtype=np.float32, rows_per_chunk=64
            )
            b_map = MatrixMultiplicationAdapter.generate_memmap_matrix(
                b_path, (dimension, dimension), seed=3002, dtype=np.float32, rows_per_chunk=64
            )
            input_workload = await capacity_adapter.prepare_from_memmaps(a_path, b_path)
            input_bytes = 2 * dimension * dimension * itemsize
            deadline = time.monotonic() + 12
            while True:
                node_stats = await client.get_cluster_stats()
                if sum(int(node.used_memory) for node in node_stats) >= input_bytes:
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError("node storage reports did not account for streamed inputs")
                await asyncio.sleep(0.25)
            assert len(node_stats) == 3
            assert all(int(node.used_memory) <= int(node.total_memory) for node in node_stats)
            used_after_inputs = {node.node_id: int(node.used_memory) for node in node_stats}
            assert len(used_after_inputs) == 3
            assert sum(value > 0 for value in used_after_inputs.values()) >= 2

            before_capacity_tasks = set(compute._results)
            output_workload = await capacity_adapter.execute(input_workload)
            deadline = time.monotonic() + 8
            while True:
                node_stats = await client.get_cluster_stats()
                if sum(int(node.used_memory) for node in node_stats) >= required_bytes:
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError("node storage reports omitted compute output allocations")
                await asyncio.sleep(0.25)
            used_during_compute = {node.node_id: int(node.used_memory) for node in node_stats}
            assert len(used_during_compute) == 3
            assert max(used_during_compute.values()) <= max(configured_per_node)
            assert sum(used_during_compute.values()) >= required_bytes
            result_map = await capacity_adapter.collect_result_to_memmap(output_workload, c_path)
            assert isinstance(result_map, np.memmap)
            assert result_map.shape == (dimension, dimension)
            # Verify in row chunks; the complete A, B, and C matrices are never
            # materialized together in the client process.
            for row in range(0, dimension, 64):
                expected_rows = np.asarray(a_map[row:row + 64]) @ np.asarray(b_map)
                np.testing.assert_allclose(result_map[row:row + 64], expected_rows,
                                           rtol=2e-4, atol=2e-4)
            del result_map
            capacity_results = [result for task_id, result in compute._results.items()
                                if task_id not in before_capacity_tasks]
            assert capacity_results and all(result.status.value == "COMPLETED"
                                            for result in capacity_results)
            assert len({result.node_id for result in capacity_results}) >= 2
            assert sum(result.metrics.get("local_read_bytes", 0)
                       for result in capacity_results) > 0
        finally:
            stop_rss.set()
            stop_storage.set()
            await rss_task
            await storage_task
            await capacity_adapter.cleanup(input_workload, output_workload)
            if a_map is not None:
                del a_map
            if b_map is not None:
                del b_map

        capacity_result = {
            "dimension": dimension,
            "dtype": "float32",
            "block_size": block_size,
            "required_dense_storage_bytes_A_B_C": required_bytes,
            "configured_capacity_per_node_bytes": configured_per_node,
            "configured_cluster_capacity_bytes": sum(configured_per_node),
            "stored_bytes_after_input_preparation_by_node": used_after_inputs,
            "stored_bytes_during_compute_by_node": used_during_compute,
            "peak_stored_bytes_by_node": storage_peak,
            "baseline_rss_bytes_by_process": baseline_rss,
            "peak_rss_bytes_by_process": peak_rss,
            "aggregate_sampled_peak_rss_bytes": aggregate_rss_peak,
            "sampling_interval_seconds": 0.05,
            "worker_node_ids": sorted({result.node_id for result in capacity_results}),
            "task_count": len(capacity_results),
            "direct_local_read_bytes": sum(result.metrics.get("local_read_bytes", 0)
                                            for result in capacity_results),
            "numerically_correct": True,
        }
        (tmp_path / "capacity-experiment.json").write_text(
            json.dumps(capacity_result, indent=2), encoding="utf-8"
        )

        matrix_benchmark = tmp_path / "matrix-256-results.json"
        matrix_command = [PYTHON, str(ROOT / "scripts/benchmark_matrix_memmap.py"),
                          "--host", "127.0.0.1", "--server-name", "localhost",
                          "--port", str(port), "--work-dir", str(tmp_path / "matrix-256"),
                          "--dimensions", "256", "--block-sizes", "128",
                          "--modes", "native", "local_tiled", "distributed_compute",
                          "--input-access-mode", "direct", "--repeats", "2",
                          "--generation-rows", "32", "--cpu-threads", "1",
                          "--worker-slots", "2", "--sample-process",
                          f"coordinator={coordinator.pid}"]
        for node_index, process in enumerate(nodes, 1):
            matrix_command.extend(("--sample-process", f"node-{node_index}={process.pid}"))
        matrix_command.extend(("--output", str(matrix_benchmark)))
        benchmark = subprocess.run(matrix_command, cwd=ROOT, env=env, capture_output=True,
                                   text=True, timeout=120)
        assert benchmark.returncode == 0, benchmark.stderr
        matrix_rows = json.loads(matrix_benchmark.read_text(encoding="utf-8"))
        measured = [row for row in matrix_rows if "repetition" in row]
        assert {(row["dimension"], row["mode"]) for row in measured} == {
            (256, "native"), (256, "local_tiled"), (256, "distributed_compute")
        }
        assert len(measured) == 6 and all(row["numerically_correct"] for row in measured)
    finally:
        if client is not None:
            await client.close()
        for process in (*nodes, coordinator):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in node_logs:
            log.close()
        coordinator_log.close()
