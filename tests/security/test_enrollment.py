import asyncio
from types import SimpleNamespace

import pytest

from common.protocol import dsm_pb2
from common.types.security import EventType, TrustState
from network.tcp.coordinator import CoordinatorServer
from security.authentication.enrollment import EnrollmentDenied, EnrollmentGuard
from security.authentication.registration import Role
from security.config import EnrollmentPolicy
from security.quarantine.trust_state import TrustStateMachine


@pytest.fixture
def enrollment(tmp_path):
    policy = EnrollmentPolicy(denylist_path=str(tmp_path / "denylist.json"))
    env = {
        policy.node_secret_env: "node-enrollment-test-secret-0123456789",
        policy.client_secret_env: "client-enrollment-test-secret-0123456789",
    }
    return policy, env, EnrollmentGuard(policy, env)


def test_registration_requires_configured_role_secret_and_node_certificate(enrollment):
    _, _, guard = enrollment
    with pytest.raises(EnrollmentDenied):
        guard.authorize(Role.NODE, "wrong", "a" * 64)
    with pytest.raises(EnrollmentDenied):
        guard.authorize(Role.NODE, "node-enrollment-test-secret-0123456789", "")
    with pytest.raises(EnrollmentDenied):
        guard.authorize(Role.NODE, "node-enrollment-test-secret-0123456789", "not-a-fingerprint")

    assert guard.authorize(Role.NODE, "node-enrollment-test-secret-0123456789", "a" * 64) is Role.NODE
    assert guard.authorize(Role.CLIENT, "client-enrollment-test-secret-0123456789", "") is Role.CLIENT


def test_missing_secrets_fail_closed(enrollment):
    policy, _, _ = enrollment
    with pytest.raises(ValueError, match="enrollment secrets must be configured"):
        EnrollmentGuard(policy, {})


def test_dev_certificate_issuer_creates_unique_node_certificates(monkeypatch, tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from scripts import gen_certs

    monkeypatch.setattr(gen_certs, "OUT", tmp_path / "certs")
    gen_certs.initialize()
    gen_certs.issue_node("node-2", *gen_certs.load_ca())
    node1 = x509.load_pem_x509_certificate((gen_certs.OUT / "nodes/node-1.pem").read_bytes())
    node2 = x509.load_pem_x509_certificate((gen_certs.OUT / "nodes/node-2.pem").read_bytes())

    assert node1.fingerprint(hashes.SHA256()) != node2.fingerprint(hashes.SHA256())


@pytest.mark.asyncio
async def test_revoked_node_certificate_is_persistently_denied(enrollment):
    policy, env, guard = enrollment
    fingerprint = "b" * 64
    assert guard.authorize(Role.NODE, env[policy.node_secret_env], fingerprint) is Role.NODE
    guard.bind_node("node-uuid", fingerprint)
    trust = TrustStateMachine(clock=lambda: 1.0)
    trust.register_revocation_listener(guard.record_revocation)
    trust.transition("node-uuid", TrustState.REVOKED, "test")

    restarted_guard = EnrollmentGuard(policy, env)
    with pytest.raises(EnrollmentDenied):
        restarted_guard.authorize(Role.NODE, env[policy.node_secret_env], fingerprint)


def test_certificate_binding_persists_and_blocks_duplicate_enrollment(tmp_path):
    from security.bootstrap import build_security_core
    from security.config import AuditPolicy, SecurityConfig
    from security.persistence.database import SecurityDatabase
    from security.persistence.repository import SecurityRepository

    policy = EnrollmentPolicy(denylist_path=str(tmp_path / "denylist.json"))
    env = {policy.node_secret_env: "node-enrollment-test-secret-0123456789",
           policy.client_secret_env: "client-enrollment-test-secret-0123456789"}
    path = tmp_path / "security.db"
    repo = SecurityRepository(SecurityDatabase(path))
    core = build_security_core(SecurityConfig(audit=AuditPolicy(path=str(tmp_path / "audit.jsonl"))),
                               repository=repo)
    fingerprint = "f" * 64
    node = core.auth.register(Role.NODE, "node-1", certificate_fingerprint=fingerprint)
    core.audit.close()
    repo.close()

    repo = SecurityRepository(SecurityDatabase(path))
    guard = EnrollmentGuard(policy, env, repo)
    assert repo.get_node_certificate(node.identity) == fingerprint
    assert guard.identity_for_reenrollment(env[policy.node_secret_env], fingerprint) == node.identity
    with pytest.raises(EnrollmentDenied):
        guard.identity_for_reenrollment("incorrect-recovery-secret", fingerprint)
    assert guard.authorize_node_reconnect(node.identity, fingerprint) is None
    with pytest.raises(EnrollmentDenied):
        guard.authorize(Role.NODE, env[policy.node_secret_env], fingerprint)
    with pytest.raises(EnrollmentDenied):
        guard.authorize_node_reconnect(node.identity, "e" * 64)
    repo.close()


def test_revoked_certificate_binding_survives_restart(enrollment, tmp_path):
    from security.bootstrap import build_security_core
    from security.config import AuditPolicy, SecurityConfig
    from security.persistence.database import SecurityDatabase
    from security.persistence.repository import SecurityRepository
    from security.errors import IdentityBlockedError

    policy, env, _ = enrollment
    path = tmp_path / "security.db"
    repo = SecurityRepository(SecurityDatabase(path))
    core = build_security_core(SecurityConfig(audit=AuditPolicy(path=str(tmp_path / "audit.jsonl"))),
                               repository=repo)
    trust = TrustStateMachine(repository=repo)
    trust.attach_audit(core.audit)
    fingerprint = "a" * 64
    node = core.auth.register(Role.NODE, "node", certificate_fingerprint=fingerprint)
    guard = EnrollmentGuard(policy, env, repo)
    trust.register_revocation_listener(guard.record_revocation)
    trust.register_revocation_listener(lambda identity, _ts: core.auth.revoke_identity(identity))
    assert trust.transition(node.identity, TrustState.SUSPICIOUS, "test")
    assert trust.transition(node.identity, TrustState.QUARANTINED, "test")
    assert core.auth.authenticate(node.identity, node.credential).identity == node.identity
    assert trust.transition(node.identity, TrustState.REVOKED, "test")
    with pytest.raises(IdentityBlockedError):
        core.auth.authenticate(node.identity, node.credential)
    core.audit.close()
    repo.close()

    repo = SecurityRepository(SecurityDatabase(path))
    restarted_guard = EnrollmentGuard(policy, env, repo)
    with pytest.raises(EnrollmentDenied):
        restarted_guard.authorize_node_reconnect(node.identity, fingerprint)
    assert repo.get_node_certificate(node.identity) == fingerprint
    repo.close()


class FakeConnection:
    def __init__(self):
        self.sent = []

    async def send(self, envelope):
        self.sent.append(envelope)


class FakeAuth:
    def __init__(self):
        self.calls = []

    def register(self, role, label, request_id, certificate_fingerprint=None):
        self.calls.append((role, label, request_id, certificate_fingerprint))
        return SimpleNamespace(identity="node-uuid", credential="one-time-credential")


class FakeAudit:
    def __init__(self):
        self.events = []

    def log_security_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


def test_coordinator_rejects_bad_secret_before_minting_identity(enrollment):
    _, _, guard = enrollment
    auth, audit, conn = FakeAuth(), FakeAudit(), FakeConnection()
    runtime = SimpleNamespace(start_generation=lambda _generation: None)
    server = CoordinatorServer("localhost", 0, auth, runtime, enrollment=guard, audit=audit)
    env = dsm_pb2.Envelope()
    env.register.request_id = "request-1"
    env.register.role = "node"
    env.register.label = "node-1"
    env.register.enrollment_secret = "incorrect"

    asyncio.run(server._handle_register(env, conn, "c" * 64, "127.0.0.1"))

    assert auth.calls == []
    assert conn.sent[0].error.code == "AUTH_REJECTED"
    assert audit.events[0][0][0] is EventType.ENROLLMENT_DENIED
    assert "incorrect" not in repr(audit.events)


def test_coordinator_binds_certificate_on_valid_node_registration(enrollment):
    policy, env_values, guard = enrollment
    auth, conn = FakeAuth(), FakeConnection()
    runtime = SimpleNamespace(start_generation=lambda _generation: None)
    server = CoordinatorServer("localhost", 0, auth, runtime, enrollment=guard)
    env = dsm_pb2.Envelope()
    env.register.request_id = "request-2"
    env.register.role = "node"
    env.register.label = "node-1"
    env.register.enrollment_secret = env_values[policy.node_secret_env]
    fingerprint = "d" * 64

    asyncio.run(server._handle_register(env, conn, fingerprint, "127.0.0.1"))

    assert auth.calls[0][0] is Role.NODE
    assert conn.sent[0].register_response.identity == "node-uuid"
    trust = TrustStateMachine(clock=lambda: 1.0)
    trust.register_revocation_listener(guard.record_revocation)
    trust.transition("node-uuid", TrustState.REVOKED, "test")
    restarted_guard = EnrollmentGuard(policy, env_values)
    with pytest.raises(EnrollmentDenied):
        restarted_guard.authorize(Role.NODE, env_values[policy.node_secret_env], fingerprint)
