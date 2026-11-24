from common.types.security import (
    EventType, Result, SecurityEvent, Severity, TrustState,
)
from security.authentication.registration import Role
from security.bootstrap import build_security_core
from security.config import AuditPolicy, SecurityConfig
from security.quarantine.trust_state import TrustStateMachine
from dsm.directory.directory import Directory, ObjectRecord, ObjectState
from dsm.runtime.runtime import Runtime
from tests.mocks.fakes import FakeNodeClient, FakeScheduler


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def event(kind, actor="node-1", timestamp=100.0):
    return SecurityEvent(
        event_id="event", timestamp=timestamp, event_type=kind,
        severity=Severity.WARNING, actor_id=actor, result=Result.REJECTED,
        request_id="request",
    )


def registered_machine(clock):
    machine = TrustStateMachine(clock=clock)
    machine.handle_event(event(EventType.NODE_REGISTERED))
    return machine


def test_auth_threshold_and_single_event_escalation():
    clock = Clock()
    machine = registered_machine(clock)
    for timestamp in (100.0, 120.0):
        machine.handle_event(event(EventType.AUTH_FAILURE, timestamp=timestamp))
    assert machine.get_trust_state("node-1") is TrustState.TRUSTED
    machine.handle_event(event(EventType.AUTH_FAILURE, timestamp=159.0))
    assert machine.get_trust_state("node-1") is TrustState.SUSPICIOUS

    machine = registered_machine(clock)
    machine.handle_event(event(EventType.REPLAY_DETECTED))
    assert machine.get_trust_state("node-1") is TrustState.SUSPICIOUS


def test_lazy_recovery_quarantine_and_terminal_revocation():
    clock = Clock()
    machine = registered_machine(clock)
    machine.handle_event(event(EventType.REPLAY_DETECTED))
    clock.now += 299
    assert machine.get_trust_state("node-1") is TrustState.SUSPICIOUS
    clock.now += 1
    assert machine.get_trust_state("node-1") is TrustState.TRUSTED

    machine.transition("node-1", TrustState.SUSPICIOUS)
    machine.handle_event(event(EventType.AUTH_FAILURE, timestamp=clock.now))
    assert machine.get_trust_state("node-1") is TrustState.QUARANTINED
    clock.now += 10_000
    assert machine.get_trust_state("node-1") is TrustState.QUARANTINED
    machine.reinstate("node-1")
    assert machine.get_trust_state("node-1") is TrustState.TRUSTED

    machine.transition("node-1", TrustState.SUSPICIOUS)
    machine.transition("node-1", TrustState.QUARANTINED)
    machine.handle_event(event(EventType.REPLAY_DETECTED, timestamp=clock.now))
    assert machine.get_trust_state("node-1") is TrustState.REVOKED
    assert not machine.transition("node-1", TrustState.TRUSTED)


def test_revocation_listener_invalidates_auth_sessions(tmp_path):
    clock = Clock()
    cfg = SecurityConfig(audit=AuditPolicy(path=str(tmp_path / "audit.jsonl")))
    core = build_security_core(cfg, clock=clock)
    machine = TrustStateMachine(clock=clock)
    machine.attach_audit(core.audit)
    machine.register_revocation_listener(
        lambda identity, _timestamp: core.auth.revoke_identity(identity, "trust_state_revoked")
    )
    try:
        registration = core.auth.register(Role.NODE, "node-1")
        authenticated = core.auth.authenticate(registration.identity, registration.credential)
        machine.transition(registration.identity, TrustState.REVOKED)
        assert core.sessions.get(authenticated.session_id).revoked
    finally:
        core.audit.close()


def test_runtime_receives_quarantine_and_reinstatement_transitions():
    clock = Clock()
    machine = registered_machine(clock)

    class RuntimeSecurity:
        def check_access(self, identity, owner, action, object_id):
            return identity == owner

        def register_revocation_listener(self, callback):
            machine.register_revocation_listener(callback)

        def register_state_listener(self, callback):
            machine.register_state_listener(callback)

    directory = Directory()
    directory.put(ObjectRecord("obj-1", "owner", 8, "node-1"))
    Runtime(directory, FakeNodeClient(), FakeScheduler(), RuntimeSecurity())

    machine.transition("node-1", TrustState.SUSPICIOUS)
    machine.transition("node-1", TrustState.QUARANTINED)
    assert directory.get("obj-1").state is ObjectState.QUARANTINED
    machine.reinstate("node-1")
    assert directory.get("obj-1").state is ObjectState.ALLOCATED


def test_revocation_closes_connection_before_notifying_listeners():
    import asyncio
    from security.quarantine.revocation import RevocationDispatcher

    async def scenario():
        order = []
        dispatcher = RevocationDispatcher()

        async def close_connection(node_id):
            await asyncio.sleep(0)
            order.append(("closed", node_id))

        dispatcher.set_force_close_connection(close_connection)
        dispatcher.register_revocation_listener(
            lambda node_id, timestamp: order.append(("notified", node_id, timestamp))
        )
        dispatcher.transition("node-uuid", 12.5)
        await asyncio.sleep(0.01)
        assert order == [("closed", "node-uuid"), ("notified", "node-uuid", 12.5)]

    asyncio.run(scenario())
