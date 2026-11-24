import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from common.types.security import EventType, Result, TrustState
from common.types.errors import TokenInvalidError
from security.authentication.registration import Role
from security.authentication.tokens import TokenService
from security.bootstrap import build_security_core
from security.config import AuditPolicy, SecurityConfig
from security.errors import IdentityBlockedError
from security.persistence.database import SecurityDatabase, SecurityPersistenceError
from security.persistence.repository import SecurityRepository
from security.quarantine.trust_state import TrustStateMachine


def repository(path):
    return SecurityRepository(SecurityDatabase(path))


def test_identity_credentials_trust_violations_and_audit_survive_new_instances(tmp_path):
    path = tmp_path / "security.db"
    repo = repository(path)
    cfg = SecurityConfig(audit=AuditPolicy(path=str(tmp_path / "audit.jsonl")),
                         token_key=TokenService.generate_key())
    core = build_security_core(cfg, repository=repo)
    trust = TrustStateMachine(repository=repo)
    trust.attach_audit(core.audit)
    client = core.auth.register(Role.CLIENT, "client")
    node = core.auth.register(Role.NODE, "node")
    trust.transition(node.identity, TrustState.SUSPICIOUS, "manual_test")
    trust.transition(node.identity, TrustState.QUARANTINED, "manual_test")
    core.audit.emit(EventType.AUTH_SUCCESS, client.identity, Result.SUCCESS, "test")
    core.audit.close()
    repo.close()

    repo2 = repository(path)
    core2 = build_security_core(cfg, repository=repo2)
    trust2 = TrustStateMachine(repository=repo2)
    assert core2.identities.verify(client.identity, client.credential) is not None
    assert trust2.get_trust_state(node.identity) is TrustState.QUARANTINED
    assert repo2.load_identities()
    assert repo2.database.run(lambda db: db.execute("SELECT count(*) FROM audit_events").fetchone()[0]) >= 3
    core2.audit.close()
    repo2.close()


def test_v1_database_migrates_without_losing_security_records(tmp_path):
    path = tmp_path / "security.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO metadata VALUES('schema_version', '1')")
    connection.execute("CREATE TABLE identities (identity TEXT PRIMARY KEY, label TEXT NOT NULL, role TEXT NOT NULL, credential_hash TEXT NOT NULL, created_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)")
    connection.execute("INSERT INTO identities VALUES('node-id','node','node','hash',1,0)")
    connection.commit()
    connection.close()

    repo = repository(path)
    assert repo.database.run(lambda db: db.execute(
        "SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]) == "2"
    assert repo.database.run(lambda db: db.execute(
        "SELECT identity FROM identities").fetchone()[0]) == "node-id"
    assert repo.database.run(lambda db: db.execute(
        "SELECT count(*) FROM node_certificates").fetchone()[0]) == 0
    repo.close()


def test_node_identity_and_certificate_binding_commit_atomically(tmp_path):
    repo = repository(tmp_path / "security.db")
    core = build_security_core(SecurityConfig(
        audit=AuditPolicy(path=str(tmp_path / "audit.jsonl"))), repository=repo)
    node = core.auth.register(Role.NODE, "node", certificate_fingerprint="a" * 64)
    assert repo.get_node_certificate(node.identity) == "a" * 64
    with pytest.raises(SecurityPersistenceError):
        core.auth.register(Role.NODE, "duplicate-cert", certificate_fingerprint="a" * 64)
    assert len(core.identities._by_id) == 1
    core.audit.close()
    repo.close()


def test_sessions_are_ephemeral_and_revoked_identity_stays_blocked_after_restart(tmp_path):
    path = tmp_path / "security.db"
    key = TokenService.generate_key()
    cfg = SecurityConfig(audit=AuditPolicy(path=str(tmp_path / "audit.jsonl")), token_key=key)
    repo1 = repository(path)
    core1 = build_security_core(cfg, repository=repo1)
    registration = core1.auth.register(Role.NODE, "node")
    auth = core1.auth.authenticate(registration.identity, registration.credential)
    client = core1.auth.register(Role.CLIENT, "client")
    client_auth = core1.auth.authenticate(client.identity, client.credential)
    core1.auth.revoke_identity(registration.identity)
    core1.audit.close()
    repo1.close()

    repo2 = repository(path)
    core2 = build_security_core(cfg, repository=repo2)
    with pytest.raises(IdentityBlockedError):
        core2.auth.authenticate(registration.identity, registration.credential)
    with pytest.raises(IdentityBlockedError):
        core2.auth.validate(auth.token, claimed_identity=registration.identity)
    with pytest.raises(TokenInvalidError):
        core2.auth.validate(client_auth.token, claimed_identity=client.identity)
    new_auth = core2.auth.authenticate(client.identity, client.credential)
    assert core2.auth.validate(new_auth.token, claimed_identity=client.identity).identity == client.identity
    assert repo2.database.run(lambda db: db.execute(
        "SELECT count(*) FROM identities WHERE identity=?", (registration.identity,)
    ).fetchone()[0]) == 1
    core2.audit.close()
    repo2.close()


def test_bans_and_audit_json_are_durable(tmp_path):
    repo = repository(tmp_path / "security.db")
    repo.add_ban("fingerprint", "node_certificate", 1.0, "revoked", "node-id")
    from common.types.security import SecurityEvent, Severity
    repo.add_audit_event(SecurityEvent("ev-1", 1.0, EventType.AUTH_FAILURE, Severity.WARNING,
                                       "node-id", Result.REJECTED, "r1", {"reason": "bad"}))
    repo.close()
    repo = repository(tmp_path / "security.db")
    assert repo.has_ban("fingerprint", "node_certificate")
    saved = repo.database.run(lambda db: db.execute(
        "SELECT event_json FROM audit_events WHERE event_id='ev-1'").fetchone()[0])
    assert json.loads(saved)["actor_id"] == "node-id"
    repo.close()


def test_suspicious_state_and_recent_violation_window_survive_restart(tmp_path):
    path = tmp_path / "security.db"
    cfg = SecurityConfig(audit=AuditPolicy(path=str(tmp_path / "audit.jsonl")))
    repo1 = repository(path)
    core = build_security_core(cfg, repository=repo1)
    trust = TrustStateMachine(repository=repo1)
    trust.attach_audit(core.audit)
    node = core.auth.register(Role.NODE, "node")
    for _ in range(3):
        core.audit.emit(EventType.AUTH_FAILURE, node.identity, Result.REJECTED, "test")
    assert trust.get_trust_state(node.identity) is TrustState.SUSPICIOUS
    core.audit.close()
    repo1.close()

    repo2 = repository(path)
    trust2 = TrustStateMachine(repository=repo2)
    assert trust2.get_trust_state(node.identity) is TrustState.SUSPICIOUS
    assert trust2.get_status(node.identity).failure_count == 3
    repo2.close()


@pytest.mark.parametrize("target", list(TrustState))
def test_each_node_trust_state_survives_restart(tmp_path, target):
    path = tmp_path / "security.db"
    repo1 = repository(path)
    cfg = SecurityConfig(audit=AuditPolicy(path=str(tmp_path / "audit.jsonl")))
    core = build_security_core(cfg, repository=repo1)
    trust = TrustStateMachine(repository=repo1)
    trust.attach_audit(core.audit)
    node = core.auth.register(Role.NODE, "node")
    if target is TrustState.SUSPICIOUS:
        trust.transition(node.identity, target, "test")
    elif target is TrustState.QUARANTINED:
        trust.transition(node.identity, TrustState.SUSPICIOUS, "test")
        trust.transition(node.identity, target, "test")
    elif target is TrustState.REVOKED:
        trust.transition(node.identity, target, "test")
    core.audit.close()
    repo1.close()

    repo2 = repository(path)
    assert TrustStateMachine(repository=repo2).get_trust_state(node.identity) is target
    repo2.close()


def test_new_registry_starts_empty_after_coordinator_restart():
    from network.tcp.registry import NodeRegistry
    from scripts.coordinator_main import DynamicNodeProvider
    assert NodeRegistry().list_ids() == []
    assert DynamicNodeProvider(NodeRegistry(), None, None, None).get_nodes() == []


def test_write_failure_does_not_cache_new_identity(tmp_path):
    repo = repository(tmp_path / "security.db")
    core = build_security_core(SecurityConfig(
        audit=AuditPolicy(path=str(tmp_path / "audit.jsonl"))), repository=repo)
    repo.close()
    with pytest.raises(SecurityPersistenceError):
        core.identities.register(Role.CLIENT, "client")
    assert core.identities._by_id == {}
    core.audit.close()


def test_certificate_binding_write_failure_does_not_cache_identity(tmp_path):
    repo = repository(tmp_path / "security.db")
    core = build_security_core(SecurityConfig(
        audit=AuditPolicy(path=str(tmp_path / "audit.jsonl"))), repository=repo)
    repo.close()
    with pytest.raises(SecurityPersistenceError):
        core.identities.register(Role.NODE, "node", certificate_fingerprint="a" * 64)
    assert core.identities._by_id == {}
    core.audit.close()


def test_failed_transaction_rolls_back_all_changes(tmp_path):
    repo = repository(tmp_path / "security.db")
    def failed_write(db):
        db.execute("INSERT INTO bans(value,kind,created_at,reason) VALUES('x','test',1,'test')")
        raise sqlite3.IntegrityError("forced rollback")
    with pytest.raises(SecurityPersistenceError):
        repo.database.run(failed_write)
    assert not repo.has_ban("x", "test")
    repo.close()


def test_concurrent_ban_updates_are_consistent(tmp_path):
    repo = repository(tmp_path / "security.db")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: repo.add_ban(str(n), "test", float(n), "test"), range(40)))
    assert repo.database.run(lambda db: db.execute(
        "SELECT count(*) FROM bans WHERE kind='test'").fetchone()[0]) == 40
    repo.close()


def test_committed_security_state_survives_abrupt_process_exit(tmp_path):
    path = tmp_path / "security.db"
    script = (
        "import os,sys; from security.persistence.database import SecurityDatabase; "
        "from security.persistence.repository import SecurityRepository; "
        "r=SecurityRepository(SecurityDatabase(sys.argv[1])); "
        "r.add_ban('committed','test',1.0,'test'); os._exit(0)"
    )
    result = subprocess.run([sys.executable, "-c", script, str(path)], cwd=os.getcwd())
    assert result.returncode == 0
    repo = repository(path)
    assert repo.has_ban("committed", "test")
    repo.close()


def test_schema_version_is_checked_without_reset(tmp_path):
    path = tmp_path / "security.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT INTO metadata VALUES('schema_version','99')")
    with pytest.raises(SecurityPersistenceError, match="schema version"):
        SecurityDatabase(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] == "99"


def test_database_contains_no_plaintext_credentials_or_tokens(tmp_path):
    repo = repository(tmp_path / "security.db")
    core = build_security_core(SecurityConfig(
        audit=AuditPolicy(path=str(tmp_path / "audit.jsonl"))), repository=repo)
    result = core.auth.register(Role.CLIENT, "client")
    auth = core.auth.authenticate(result.identity, result.credential)
    contents = repo.database.run(lambda db: repr((
        db.execute("SELECT * FROM identities").fetchall(),
        db.execute("SELECT * FROM audit_events").fetchall(),
    )))
    assert result.credential not in contents and auth.token not in contents
    core.audit.close()
    repo.close()


def test_database_missing_after_first_initialization_fails_closed(tmp_path):
    path = tmp_path / "security.db"
    db = SecurityDatabase(path)
    db.close()
    path.unlink()
    with pytest.raises(SecurityPersistenceError, match="missing"):
        SecurityDatabase(path)


def test_corrupt_database_fails_closed_without_replacement(tmp_path):
    path = tmp_path / "security.db"
    path.write_bytes(b"not a sqlite database")
    with pytest.raises(SecurityPersistenceError):
        SecurityDatabase(path)
    assert path.read_bytes() == b"not a sqlite database"
