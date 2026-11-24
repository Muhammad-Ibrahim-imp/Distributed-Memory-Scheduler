"""Single-node DSM smoke test using the real coordinator, node, and TLS client."""
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import ssl
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
import cv2
import numpy as np
from cryptography.fernet import Fernet

from application.image_processing.image_adapter import ImageProcessingAdapter
from application.matrix_multiplication.matrix_adapter import MatrixMultiplicationAdapter
from application.checksum import ChecksumAdapter
from common.types.errors import (
    NotOwnerError, ObjectNotFoundError, ObjectUnavailableError, QuotaExceededError,
    TokenInvalidError,
)
from common.types.security import TrustState
from common.protocol import dsm_pb2
from network.security.tls import client_context
from network.tcp.connection import Connection
from network.tcp.dsmapi_stub import RemoteDSMAPI


ROOT = Path(__file__).resolve().parents[3]
PYTHON = sys.executable
pytestmark = [pytest.mark.integration, pytest.mark.full_stack]


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for(predicate, description, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if await value if asyncio.iscoroutine(value) else value:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {description}")


async def _send_bad_node_refresh(port, cert_dir, node_index, node_identity):
    node_id = f"node-{node_index}"
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", port,
        ssl=client_context(cert_dir / f"nodes/{node_id}.pem", cert_dir / f"nodes/{node_id}.key"),
        server_hostname="coordinator",
    )
    connection = Connection(reader, writer)
    connection.start()
    env = dsm_pb2.Envelope()
    env.protocol_version = 1
    env.timestamp = time.time()
    env.refresh.request_id = f"failed-refresh-{time.time_ns()}"
    env.refresh.identity = node_identity
    env.refresh.credential = "incorrect-node-credential"
    try:
        response = await connection.call(env)
        assert response.WhichOneof("payload") == "error"
        assert response.error.code == "AUTH_REJECTED"
    finally:
        await connection.close()


async def _send_node_registration(port, cert_dir, node_index, label, secret):
    cert_name = f"node-{node_index}"
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", port,
        ssl=client_context(cert_dir / f"nodes/{cert_name}.pem",
                           cert_dir / f"nodes/{cert_name}.key"),
        server_hostname="coordinator",
    )
    connection = Connection(reader, writer)
    connection.start()
    env = dsm_pb2.Envelope()
    env.protocol_version = 1
    env.timestamp = time.time()
    env.nonce = f"registration-{time.time_ns()}"
    env.register.request_id = f"register-{time.time_ns()}"
    env.register.role = "node"
    env.register.label = label
    env.register.enrollment_secret = secret
    try:
        return await connection.call(env)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_real_coordinator_node_and_authenticated_client(tmp_path, monkeypatch):
    cert_cwd = tmp_path / "cert-work"
    cert_cwd.mkdir()
    subprocess.run([PYTHON, str(ROOT / "scripts/gen_certs.py")], cwd=cert_cwd,
                   check=True, capture_output=True, text=True, timeout=30)
    cert_dir = cert_cwd / "certs"
    for node_id in ("node-2", "node-3"):
        subprocess.run([PYTHON, str(ROOT / "scripts/gen_certs.py"), "--issue-node", node_id],
                       cwd=cert_cwd, check=True, capture_output=True, text=True, timeout=30)
    monkeypatch.setenv("DSM_CERT_DIR", str(cert_dir))

    secret = "full-stack-test-enrollment-secret"
    security = yaml.safe_load((ROOT / "configs/security.yaml").read_text())
    security["token"]["ttl_seconds"] = 10
    security["persistence"]["database_path"] = str(tmp_path / "security.db")
    security["audit"]["path"] = str(tmp_path / "audit.jsonl")
    security["enrollment"]["denylist_path"] = str(tmp_path / "denylist.json")
    security_path = tmp_path / "security.yaml"
    security_path.write_text(yaml.safe_dump(security))

    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1",
        "DSM_CERT_DIR": str(cert_dir),
        "DSM_SECURITY_CONFIG": str(security_path),
        "DSM_NODE_ENROLLMENT_SECRET": secret,
        "DSM_CLIENT_ENROLLMENT_SECRET": secret,
        "DSM_TOKEN_KEY": Fernet.generate_key().decode("ascii"),
        "NODE_ID": "integration-node-1",
        "NODE_CAPACITY_BYTES": str(16 * 1024 * 1024),
        "NODE_HEADROOM_FRACTION": "0.01",
        "NODE_HEADROOM_MIN_BYTES": "0",
        "NODE_HEARTBEAT_INTERVAL": "0.2",
    })
    port = _free_port()
    env["SECURITY_HOST"] = "127.0.0.1"
    env["SECURITY_PORT"] = str(port)
    coordinator_log = (tmp_path / "coordinator.log").open("w+")
    node_logs = []
    nodes = []
    coordinator = subprocess.Popen(
        [PYTHON, str(ROOT / "scripts/coordinator_main.py")], cwd=ROOT,
        env=env, stdout=coordinator_log, stderr=subprocess.STDOUT,
    )
    client = None
    try:
        async def coordinator_ready():
            if coordinator.poll() is not None:
                coordinator_log.flush()
                raise AssertionError(f"coordinator exited: {coordinator_log.name}")
            try:
                _, writer = await asyncio.open_connection(
                    "127.0.0.1", port,
                    ssl=ssl.create_default_context(cafile=str(cert_dir / "ca.pem")),
                    server_hostname="localhost",
                )
                writer.close()
                await writer.wait_closed()
                return True
            except (OSError, asyncio.TimeoutError):
                return False

        await _wait_for(coordinator_ready, "coordinator TLS readiness")
        for index, node_id in enumerate(("node-1", "node-2", "node-3"), 1):
            node_env = env.copy()
            node_env["NODE_ID"] = f"integration-{node_id}"
            node_env["DSM_NODE_CERT_FILE"] = str(cert_dir / f"nodes/{node_id}.pem")
            node_env["DSM_NODE_KEY_FILE"] = str(cert_dir / f"nodes/{node_id}.key")
            log = (tmp_path / f"node-{index}.log").open("w+")
            node_logs.append(log)
            nodes.append(subprocess.Popen(
                [PYTHON, str(ROOT / "scripts/node_main.py")], cwd=ROOT,
                env=node_env, stdout=log, stderr=subprocess.STDOUT,
            ))

        def registered_all():
            return all("[node] registered as " in (tmp_path / f"node-{i}.log").read_text(errors="replace")
                       for i in range(1, 4))

        await _wait_for(registered_all, "three authenticated memory-node registrations")
        duplicate = await _send_node_registration(port, cert_dir, 1, "duplicate-cert", secret)
        assert duplicate.WhichOneof("payload") == "error"
        assert duplicate.error.code == "AUTH_REJECTED"
        bad_client = RemoteDSMAPI("127.0.0.1", port, "bad-enrollment", "wrong-secret")
        try:
            with pytest.raises(RuntimeError, match="register failed"):
                await bad_client.connect()
        finally:
            await bad_client.close()

        client = RemoteDSMAPI("127.0.0.1", port, "full-stack-test-client", secret)
        await asyncio.wait_for(client.connect(), timeout=10)
        assert client.identity and client.token
        initial_token = client.token

        # Each object consumes half a node's configured pool, so one client's
        # per-node quota forces the scheduler to choose a different live node.
        placement_ids = []
        for _ in range(3):
            placement_ids.append(await asyncio.wait_for(client.alloc(8 * 1024 * 1024), timeout=15))
            for object_id in placement_ids:
                await client.write(object_id, b"placement-probe")
            coordinator_log.flush()
            generation_match = re.search(
                r"generation=([0-9a-f-]{36})", (tmp_path / "coordinator.log").read_text(errors="replace")
            )
            assert generation_match, "coordinator did not log its active generation"
            assert all(object_id.startswith(generation_match.group(1) + ".")
                       for object_id in placement_ids)
        await _wait_for(
            lambda: all(
                    any(f"write {oid}" in (tmp_path / f"node-{i}.log").read_text(errors="replace")
                        for i in range(1, 4)) for oid in placement_ids
            ),
            "allocations to reach node processes",
        )
        placements = {
            i for i in range(1, 4)
            if any(f"write {oid}" in (tmp_path / f"node-{i}.log").read_text(errors="replace")
                   for oid in placement_ids)
        }
        assert len(placements) == 3, f"allocations did not span three nodes: {placements}"
        node_logs_text = {
            i: (tmp_path / f"node-{i}.log").read_text(errors="replace")
            for i in range(1, 4)
        }
        target_index = next(
            i for i, log_text in node_logs_text.items() if f"write {placement_ids[0]}" in log_text
        )
        target_node_identity = next(
            line.split("registered as ", 1)[1]
            for line in node_logs_text[target_index].splitlines()
            if "[node] registered as " in line
        )
        await client.write(placement_ids[0], b"quarantine-readable")

        # Three bad credentials trigger SUSPICIOUS; the next violation
        # follows the policy transition to QUARANTINED.
        for _ in range(4):
            await _send_bad_node_refresh(port, cert_dir, target_index, target_node_identity)
        with pytest.raises(ObjectUnavailableError):
            await client.write(placement_ids[0], b"blocked")
        assert await client.read(placement_ids[0]) == b"quarantine-readable"
        quarantine_index = next(index for index in range(1, 4) if index != target_index)
        quarantine_object = next(
            oid for oid in placement_ids
            if f"write {oid}" in (tmp_path / f"node-{quarantine_index}.log").read_text(errors="replace")
        )
        quarantine_identity = next(
            line.split("registered as ", 1)[1]
            for line in (tmp_path / f"node-{quarantine_index}.log").read_text(errors="replace").splitlines()
            if "[node] registered as " in line
        )
        await client.write(quarantine_object, b"quarantine-survives-restart")
        for oid in placement_ids[1:]:
            if oid != quarantine_object:
                await client.free(oid)
        for _ in range(4):
            await _send_bad_node_refresh(port, cert_dir, quarantine_index, quarantine_identity)
        assert await client.read(quarantine_object) == b"quarantine-survives-restart"
        with pytest.raises(ObjectUnavailableError):
            await client.write(quarantine_object, b"blocked while quarantined")
        quarantined_placement = await client.alloc(8 * 1024 * 1024)
        quarantined_logs = {
            i: (tmp_path / f"node-{i}.log").read_text(errors="replace")
            for i in range(1, 4)
        }
        assert quarantined_placement not in quarantined_logs[target_index]
        assert quarantined_placement not in quarantined_logs[quarantine_index]
        await client.free(quarantined_placement)

        owned_id = await client.alloc(32)
        await client.write(owned_id, b"owner-only")
        unauthorized = RemoteDSMAPI("127.0.0.1", port, "second-client", secret)
        try:
            await unauthorized.connect()
            with pytest.raises(NotOwnerError):
                await unauthorized.read(owned_id)
            unauthorized.token = "invalid-session-token"
            with pytest.raises(TokenInvalidError):
                await unauthorized.read(owned_id)
        finally:
            await unauthorized.close()

        replay = client._env()
        replay.read.request_id = client._rid()
        replay.read.object_id = owned_id
        replay.read.owner_identity = client.identity
        assert (await client.connection.call(replay)).WhichOneof("payload") == "read_response"
        replay_response = await client.connection.call(replay)
        assert replay_response.WhichOneof("payload") == "error"
        assert replay_response.error.code == "AUTH_REJECTED"
        with pytest.raises(QuotaExceededError):
            await client.alloc(9 * 1024 * 1024)

        # A further authenticated-node security violation while quarantined
        # promotes the node to terminal revocation and closes its live stream.
        await _send_bad_node_refresh(port, cert_dir, target_index, target_node_identity)
        async def revoked_object():
            try:
                await client.read(placement_ids[0])
                return False
            except ObjectUnavailableError:
                return True
        await _wait_for(revoked_object, "revocation update in the object Directory")
        revoked_registration = await _send_node_registration(
            port, cert_dir, target_index, "revoked-cert-reenrollment", secret
        )
        assert revoked_registration.WhichOneof("payload") == "error"
        assert revoked_registration.error.code == "AUTH_REJECTED"

        old_identity, old_token = client.identity, client.token
        await client.close()
        coordinator.terminate()
        coordinator.wait(timeout=5)
        coordinator = subprocess.Popen(
            [PYTHON, str(ROOT / "scripts/coordinator_main.py")], cwd=ROOT,
            env=env, stdout=coordinator_log, stderr=subprocess.STDOUT,
        )
        await _wait_for(coordinator_ready, "restarted coordinator TLS readiness")
        await _wait_for(
            lambda: all(
                i == target_index or "[node] reauthenticated as " in
                (tmp_path / f"node-{i}.log").read_text(errors="replace")
                for i in range(1, 4)
            ),
            "trusted/quarantined nodes to reauthenticate after restart",
            timeout=20,
        )

        await client.connect()
        assert client.identity == old_identity
        with sqlite3.connect(tmp_path / "security.db") as database:
            state = database.execute(
                "SELECT state FROM trust_states WHERE identity = ?", (quarantine_identity,)
            ).fetchone()
        assert state and state[0] == TrustState.QUARANTINED.value
        fresh_token = client.token
        client.token = old_token
        with pytest.raises(TokenInvalidError):
            await client.read(owned_id)
        client.token = fresh_token
        with pytest.raises(ObjectNotFoundError):
            await client.read(owned_id)
        after_restart = await client.alloc(16)
        await _wait_for(
            lambda: any(
                after_restart in (tmp_path / f"node-{i}.log").read_text(errors="replace")
                for i in range(1, 4)
            ),
            "post-restart allocation to reach a node",
        )
        assert after_restart not in (tmp_path / f"node-{target_index}.log").read_text(errors="replace")
        assert after_restart not in (tmp_path / f"node-{quarantine_index}.log").read_text(errors="replace")
        await client.write(after_restart, b"after restart")
        assert await client.read(after_restart) == b"after restart"
        await client.free(after_restart)

        object_ids = []
        try:
            for payload in (b"", b"known payload", bytes(range(256)) * 4):
                object_id = await asyncio.wait_for(client.alloc(max(1, len(payload))), timeout=10)
                object_ids.append(object_id)
                await client.write(object_id, payload)
                assert await client.read(object_id) == payload
            object_id = object_ids.pop()
            await client.free(object_id)
            with pytest.raises(Exception):
                await client.read(object_id)
        finally:
            for object_id in object_ids:
                await client.free(object_id)

        image = np.arange(9 * 11 * 3, dtype=np.uint8).reshape(9, 11, 3)
        for operation, halo in (("invert", 0), ("grayscale", 0), ("blur", 2)):
            adapter = ImageProcessingAdapter(
                client, operation=operation, tile_height=4, tile_width=5, halo=halo,
            )
            workload = await adapter.prepare(image)
            result_workload = None
            try:
                result_workload = await adapter.execute(workload)
                result = await adapter.collect_result(result_workload)
                if operation == "invert":
                    expected = ~image
                elif operation == "grayscale":
                    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    expected = np.repeat(gray[:, :, None], 3, axis=2)
                else:
                    expected = cv2.GaussianBlur(image, (5, 5), 0)
                np.testing.assert_array_equal(result, expected)
            finally:
                await adapter.cleanup(workload, result_workload)

        expired = client._env()
        expired.token = initial_token
        expired.read.request_id = client._rid()
        expired.read.object_id = placement_ids[0]
        expired.read.owner_identity = client.identity
        expired_response = await client.connection.call(expired)
        assert expired_response.WhichOneof("payload") == "error"
        assert expired_response.error.code == "TOKEN_EXPIRED"

        matrix_adapter = MatrixMultiplicationAdapter(client, block_size=2)
        a = np.arange(15, dtype=np.float64).reshape(5, 3)
        b = np.arange(12, dtype=np.float64).reshape(3, 4)
        workload = await matrix_adapter.prepare((a, b))
        result_workload = None
        try:
            result_workload = await matrix_adapter.execute(workload)
            np.testing.assert_allclose(await matrix_adapter.collect_result(result_workload), a @ b)
        finally:
            await matrix_adapter.cleanup(workload, result_workload)

        checksum_payload = b"third adapter over real DSM\x00\xff"
        assert await ChecksumAdapter(client).run(checksum_payload) == hashlib.sha256(
            checksum_payload
        ).hexdigest()

        benchmark_output = tmp_path / "benchmark-smoke.json"
        benchmark = subprocess.run(
            [PYTHON, str(ROOT / "scripts/benchmark_remote.py"),
             "--host", "127.0.0.1", "--port", str(port),
             "--sizes", "128", "--max-bytes", "1024",
             "--repeats", "1", "--image-size", "8", "--tile-size", "4",
             "--block-size", "2", "--format", "json", "--output", str(benchmark_output)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        assert benchmark.returncode == 0, benchmark.stderr
        benchmark_rows = json.loads(benchmark_output.read_text())
        assert {row["workload"] for row in benchmark_rows} >= {
            "remote_read_write", "checksum_adapter", "image_invert", "matrix_multiply"
        }

        audit_path = tmp_path / "audit.jsonl"
        assert audit_path.exists()
        audit_text = audit_path.read_text(errors="replace")
        assert client.identity in audit_text
        assert "enrollment_denied" in audit_text
        assert "replay_detected" in audit_text
        assert (tmp_path / "security.db").exists()
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
        for handle in (*node_logs, coordinator_log):
            handle.close()
