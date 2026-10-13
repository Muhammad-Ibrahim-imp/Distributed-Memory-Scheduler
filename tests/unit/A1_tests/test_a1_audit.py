import pytest

from memory_node.security.audit import SecurityAuditReporter
from common.types.security import EventType, Severity, Result, SecurityEvent


def make_reporter():
    r = SecurityAuditReporter("node-1")
    sent = []
    r._send = sent.append      # replaces the unfinished delivery step
    return r, sent


def test_tamper_event():
    r, sent = make_reporter()
    r.report_tamper_detected("obj-1", "req-1")
    e = sent[0]
    assert e.event_type == EventType.TAMPER_DETECTED
    assert e.severity == Severity.CRITICAL
    assert e.result == Result.FAILURE
    assert e.actor_id == "node-1"
    assert e.request_id == "req-1"
    assert e.extra == {"object_id": "obj-1"}
    assert e.event_id.startswith("evt-")


def test_authz_denied_events():
    r, sent = make_reporter()
    r.report_authz_denied("client-a", "req-2", "quota")
    r.report_authz_denied("client-a", "req-3", "not_owner", "obj-1")
    assert sent[0].event_type == EventType.AUTHZ_DENIED
    assert sent[0].severity == Severity.WARNING
    assert sent[0].result == Result.REJECTED
    assert sent[0].actor_id == "client-a"
    assert sent[0].extra == {"reason": "quota"}
    assert sent[1].extra == {"reason": "not_owner", "object_id": "obj-1"}


def test_secure_delete_failed_event():
    r, sent = make_reporter()
    r.report_secure_delete_failed("obj-1", "req-4")
    e = sent[0]
    assert e.event_type == EventType.SECURE_DELETE_FAILED
    assert e.severity == Severity.WARNING
    assert e.extra == {"object_id": "obj-1"}


def test_forbidden_extra_key_is_rejected():
    with pytest.raises(ValueError):
        SecurityEvent.create(EventType.TAMPER_DETECTED, "node-1",
                             Severity.CRITICAL, Result.FAILURE, "req-1",
                             data="raw bytes")