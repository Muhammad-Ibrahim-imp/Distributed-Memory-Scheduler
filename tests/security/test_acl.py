import pytest

from common.types.security import EventType, Result, SecurityEvent, Severity
from security.authorization.acl import AccessControl, Action

ALICE, BOB = "id-alice", "id-bob"


@pytest.fixture
def recorded():
    return []


@pytest.fixture
def acl(recorded):
    return AccessControl(log_event=lambda **kw: recorded.append(kw))


@pytest.mark.parametrize("action", list(Action))
def test_owner_allowed_and_not_logged(acl, recorded, action):
    acl.register_object("obj-1", ALICE)
    assert acl.check_access(ALICE, "obj-1", action) is True
    assert recorded == []


@pytest.mark.parametrize("action", list(Action))
def test_other_identity_denied_and_audited(acl, recorded, action):
    acl.register_object("obj-1", ALICE)
    assert acl.check_access(BOB, "obj-1", action, request_id="req-7") is False
    (event,) = recorded
    assert event["event_type"] is EventType.AUTHZ_DENIED
    assert event["actor_id"] == BOB
    assert event["severity"] is Severity.WARNING
    assert event["result"] is Result.REJECTED
    assert event["request_id"] == "req-7"
    assert event["object_id"] == "obj-1"
    assert event["action"] == action.value
    assert event["reason"] == "not_owner"


def test_unknown_object_denied(acl, recorded):
    assert acl.check_access(ALICE, "missing") is False
    assert recorded[0]["reason"] == "unknown_object"


def test_empty_identity_denied(acl, recorded):
    acl.register_object("obj-1", ALICE)
    assert acl.check_access("", "obj-1") is False
    assert recorded[0]["actor_id"] == "unknown"


def test_unregister_removes_access(acl):
    acl.register_object("obj-1", ALICE)
    acl.unregister_object("obj-1")
    assert acl.check_access(ALICE, "obj-1") is False
    acl.unregister_object("obj-1")              # idempotent, no error


def test_cannot_steal_ownership(acl):
    acl.register_object("obj-1", ALICE)
    with pytest.raises(ValueError):
        acl.register_object("obj-1", BOB)
    assert acl.owner_of("obj-1") == ALICE
    acl.register_object("obj-1", ALICE)         # same owner again is fine


@pytest.mark.parametrize("obj,owner", [("", ALICE), ("obj-1", "")])
def test_register_requires_both_fields(acl, obj, owner):
    with pytest.raises(ValueError):
        acl.register_object(obj, owner)


def test_objects_owned_by(acl):
    acl.register_object("a", ALICE)
    acl.register_object("b", ALICE)
    acl.register_object("c", BOB)
    assert acl.objects_owned_by(ALICE) == {"a", "b"}


def test_denial_builds_a_valid_security_event():
    """The sink kwargs must match log_security_event / SecurityEvent.create."""
    events = []
    acl = AccessControl(log_event=lambda **kw: events.append(SecurityEvent.create(**kw)))
    acl.register_object("obj-1", ALICE)
    acl.check_access(BOB, "obj-1", Action.WRITE)
    assert events[0].to_dict()["event_type"] == "authz_denied"
    assert events[0].extra["reason"] == "not_owner"


def test_audit_failure_fails_closed():
    def broken(**_):
        raise RuntimeError("disk full")

    acl = AccessControl(log_event=broken)
    acl.register_object("obj-1", ALICE)
    with pytest.raises(RuntimeError):          # the request fails; it is never allowed
        acl.check_access(BOB, "obj-1")


def test_works_without_an_audit_sink():
    acl = AccessControl()
    acl.register_object("obj-1", ALICE)
    assert acl.check_access(BOB, "obj-1") is False