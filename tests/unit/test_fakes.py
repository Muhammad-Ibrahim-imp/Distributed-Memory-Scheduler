import pytest

from common.types.errors import TokenInvalidError
from common.types.placement import PlacementDecision
from common.types.security import EventType, Result, Severity, TrustState
from tests.mocks.fakes import FakeScheduler, FakeSecurity


def test_fake_scheduler_returns_placement_decision():
    decision = FakeScheduler().select_node(
        object_id="obj-1",
        object_size=5,
        nodes=[],
    )

    assert decision == PlacementDecision(object_id="obj-1", node_id="node-1")


def test_fake_security_defaults_to_trusted_and_tracks_states():
    security = FakeSecurity()
    security.trust_states["node-2"] = TrustState.QUARANTINED

    assert security.get_trust_state("unknown-node") is TrustState.TRUSTED
    assert security.get_trust_state("node-2") is TrustState.QUARANTINED


def test_fake_security_logs_event_and_verifies_configured_token():
    security = FakeSecurity(token_identities={"token-1": "alice"})
    event = security.log_security_event(
        EventType.AUTH_SUCCESS,
        actor_id="alice",
        severity=Severity.INFO,
        result=Result.SUCCESS,
        request_id="req-1",
    )

    assert security.verify_token("token-1") == "alice"
    assert security.events == [event]


def test_fake_security_rejects_unknown_token():
    with pytest.raises(TokenInvalidError):
        FakeSecurity().verify_token("unknown")
