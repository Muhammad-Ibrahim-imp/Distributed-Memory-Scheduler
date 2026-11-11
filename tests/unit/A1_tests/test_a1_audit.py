import logging

import pytest

from memory_node.security.audit import SecurityAuditReporter
from common.types.security import EventType, Severity, Result, SecurityEvent

HAS_INTEGRITY_TYPE = hasattr(EventType, "OBJECT_INTEGRITY_FAILED")


def make_reporter():
    r = SecurityAuditReporter("node-1")
    sent = []
    r._send = sent.append      # replaces delivery to M5
    return r, sent


@pytest.mark.skipif(not HAS_INTEGRITY_TYPE,
                    reason="M5 has not added OBJECT_INTEGRITY_FAILED yet")
def test_integrity_failed_event():
    r, sent = make_reporter()
    r.report_integrity_failed("obj-1", "req-1")
    e = sent[0]
    assert e.event_type == EventType.OBJECT_INTEGRITY_FAILED
    assert e.severity == Severity.CRITICAL
    assert e.result == Result.FAILURE
    assert e.actor_id == "node-1"
    assert e.request_id == "req-1"
    assert e.extra == {"object_id": "obj-1"}


@pytest.mark.skipif(HAS_INTEGRITY_TYPE, reason="the shared event type now exists")
def test_integrity_failed_is_logged_locally_until_the_type_exists(caplog):
    r, sent = make_reporter()
    with caplog.at_level(logging.CRITICAL, logger="memory_node.audit"):
        r.report_integrity_failed("obj-1", "req-1")
    assert sent == []                  # nothing sent, and never TAMPER_DETECTED
    assert "object integrity failure" in caplog.text


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


def test_secure_delete_failed_includes_owner_when_given():
    r, sent = make_reporter()
    r.report_secure_delete_failed("obj-1", "req-4", "client-a")
    assert sent[0].extra == {"object_id": "obj-1", "owner": "client-a"}


def test_forbidden_extra_key_is_rejected():
    with pytest.raises(ValueError):
        SecurityEvent.create(EventType.AUTHZ_DENIED, "node-1",
                             Severity.WARNING, Result.REJECTED, "req-1",
                             data="raw bytes")


def test_delivery_calls_m5_entry_point(monkeypatch):
    import security.monitoring.audit as m5_audit
    calls = []
    monkeypatch.setattr(m5_audit, "log_security_event",
                        lambda *a, **k: calls.append((a, k)))
    SecurityAuditReporter("node-1").report_secure_delete_failed(
        "obj-1", "req-4", "client-a")
    args, kwargs = calls[0]
    assert args[0] == EventType.SECURE_DELETE_FAILED
    assert args[1] == "node-1"
    assert args[2] == Severity.WARNING
    assert args[3] == Result.FAILURE
    assert args[4] == "req-4"
    assert kwargs == {"object_id": "obj-1", "owner": "client-a"}


def test_delivery_failure_falls_back_to_local_log(monkeypatch, caplog):
    import security.monitoring.audit as m5_audit

    def boom(*a, **k):
        raise RuntimeError("audit logger not configured")

    monkeypatch.setattr(m5_audit, "log_security_event", boom)
    with caplog.at_level(logging.INFO, logger="memory_node.audit"):
        SecurityAuditReporter("node-1").report_secure_delete_failed("obj-1", "req-9")
    assert "secure_delete_failed" in caplog.text