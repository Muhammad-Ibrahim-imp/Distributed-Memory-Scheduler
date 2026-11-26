"""Real mTLS daily shutdown/restart recovery for persistent node identities."""
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[3]
PYTHON = sys.executable


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait(predicate, message, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {message}")


@pytest.mark.asyncio
async def test_three_nodes_recover_same_identities_after_full_process_restart(tmp_path):
    cert_root = tmp_path / "cert-work"
    cert_root.mkdir()
    subprocess.run([PYTHON, str(ROOT / "scripts/gen_certs.py")], cwd=cert_root,
                   check=True, capture_output=True, timeout=30)
    for label in ("node-2", "node-3"):
        subprocess.run([PYTHON, str(ROOT / "scripts/gen_certs.py"), "--issue-node", label],
                       cwd=cert_root, check=True, capture_output=True, timeout=30)
    cert_dir = cert_root / "certs"

    cfg = yaml.safe_load((ROOT / "configs/security.yaml").read_text())
    cfg["persistence"]["database_path"] = str(tmp_path / "security.db")
    cfg["audit"]["path"] = str(tmp_path / "audit.jsonl")
    cfg["enrollment"]["denylist_path"] = str(tmp_path / "denylist.json")
    config_path = tmp_path / "security.yaml"
    config_path.write_text(yaml.safe_dump(cfg))
    port = _port()
    secret = "daily-restart-enrollment-secret-32-bytes"
    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1", "DSM_CERT_DIR": str(cert_dir),
        "DSM_SECURITY_CONFIG": str(config_path),
        "DSM_NODE_ENROLLMENT_SECRET": secret, "DSM_CLIENT_ENROLLMENT_SECRET": secret,
        "DSM_TOKEN_KEY": Fernet.generate_key().decode("ascii"),
        "SECURITY_HOST": "127.0.0.1", "SECURITY_PORT": str(port),
        "NODE_CAPACITY_BYTES": str(16 * 1024 * 1024),
        "NODE_HEADROOM_FRACTION": "0.01", "NODE_HEADROOM_MIN_BYTES": "0",
        "NODE_HEARTBEAT_INTERVAL": "0.2",
    })
    log_files = []
    processes = []

    def start(script, name, node_id=None):
        child_env = env.copy()
        if node_id:
            child_env.update({
                "NODE_ID": node_id,
                "DSM_NODE_CERT_FILE": str(cert_dir / "nodes" / f"{node_id.removeprefix('integration-')}.pem"),
                "DSM_NODE_KEY_FILE": str(cert_dir / "nodes" / f"{node_id.removeprefix('integration-')}.key"),
            })
        stream = (tmp_path / f"{name}.log").open("a", encoding="utf-8")
        log_files.append(stream)
        child = subprocess.Popen([PYTHON, str(ROOT / script)], cwd=ROOT,
                                 env=child_env, stdout=stream, stderr=subprocess.STDOUT)
        processes.append(child)
        return child

    def stop_all():
        for child in reversed(processes):
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
        processes.clear()
        for stream in log_files:
            stream.flush()

    try:
        start("scripts/coordinator_main.py", "coordinator-first")
        await asyncio.sleep(1)
        for index in range(1, 4):
            start("scripts/node_main.py", f"node-{index}", f"integration-node-{index}")
        await _wait(lambda: all(
            (tmp_path / f"node-{i}.log").exists() and
            "[node] registered as " in (tmp_path / f"node-{i}.log").read_text(errors="replace")
            for i in range(1, 4)), "initial node registrations")
        identities = {}
        for i in range(1, 4):
            line = next(line for line in (tmp_path / f"node-{i}.log").read_text().splitlines()
                        if "[node] registered as " in line)
            identities[i] = line.rsplit(" ", 1)[1]
        stop_all()

        start("scripts/coordinator_main.py", "coordinator-restarted")
        await asyncio.sleep(1)
        for index in range(1, 4):
            start("scripts/node_main.py", f"node-{index}", f"integration-node-{index}")
        await _wait(lambda: all(
            (tmp_path / f"node-{i}.log").read_text(errors="replace").count("[node] registered as ") >= 2
            for i in range(1, 4)), "same-identity recovery for three restarted processes")
        for i in range(1, 4):
            recovered = [line.rsplit(" ", 1)[1]
                         for line in (tmp_path / f"node-{i}.log").read_text().splitlines()
                         if "[node] registered as " in line]
            assert recovered == [identities[i], identities[i]]

        import sqlite3
        with sqlite3.connect(tmp_path / "security.db") as db:
            assert db.execute("SELECT count(*) FROM identities WHERE role='node'").fetchone()[0] == 3
            assert db.execute("SELECT count(*) FROM audit_events WHERE event_type='node_registered'").fetchone()[0] == 3
            assert db.execute("SELECT count(*) FROM trust_states WHERE state='TRUSTED'").fetchone()[0] == 3
        first = (tmp_path / "coordinator-first.log").read_text(errors="replace")
        restarted = (tmp_path / "coordinator-restarted.log").read_text(errors="replace")
        import re
        first_generation = re.search(r"generation=([0-9a-f-]{36})", first).group(1)
        restarted_generation = re.search(r"generation=([0-9a-f-]{36})", restarted).group(1)
        assert first_generation != restarted_generation
        assert restarted.count("[auth] returning node recovered") == 3
    finally:
        stop_all()
        for stream in log_files:
            stream.close()
