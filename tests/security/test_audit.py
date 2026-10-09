import pytest
from common.types.security import EventType, Result, Severity
from security.monitoring import audit as audit_mod
from security.monitoring.audit import AuditLogger, token_ref

pytestmark = pytest.mark.security


def test_writes_policy_schema_as_jsonl(tmp_path, clock):
    p = tmp_path / "a.jsonl"
    log = AuditLogger(p, clock=clock)
    log.log_security_event(EventType.AUTH_FAILURE, "node-3", Severity.WARNING, Result.REJECTED,
                           "req-9", reason="bad")
    log.close()
    (e,) = list(AuditLogger.read_events(p))
    assert {"event_id", "timestamp", "event_type", "severity", "actor_id", "result", "request_id"} <= set(e)
    assert e["event_type"] == "auth_failure" and e["actor_id"] == "node-3"
    assert e["timestamp"] == clock.now and e["extra"] == {"reason": "bad"}


def test_forbidden_keys_are_refused(tmp_path):
    log = AuditLogger(tmp_path / "a.jsonl")
    for key in ("token", "credential", "password", "secret", "payload", "data"):
        with pytest.raises(ValueError):
            log.log_security_event(EventType.AUTH_SUCCESS, "x", Severity.INFO, Result.SUCCESS, "r", **{key: "v"})
    log.close()
    assert (tmp_path / "a.jsonl").read_text() == ""


def test_token_ref_is_short_and_not_the_token():
    assert len(token_ref("abc.def")) == 8 and "abc" not in token_ref("abc.def")


def test_rotation(tmp_path):
    p = tmp_path / "a.jsonl"
    log = AuditLogger(p, max_bytes=500, backup_count=2)
    for i in range(60):
        log.emit(EventType.AUTH_SUCCESS, "x", Result.SUCCESS, f"r{i}")
    log.close()
    assert (tmp_path / "a.jsonl.1").exists() and not (tmp_path / "a.jsonl.3").exists()


def test_listener_called_once_and_failure_isolated(tmp_path):
    log = AuditLogger(tmp_path / "a.jsonl")
    seen = []
    log.add_listener(lambda e: 1 / 0)
    log.add_listener(seen.append)
    log.emit(EventType.REPLAY_DETECTED, "n1", Result.REJECTED, None)
    log.close()
    assert len(seen) == 1 and seen[0].severity is Severity.CRITICAL and seen[0].request_id == "-"


def test_module_level_entry_point(tmp_path):
    with pytest.raises(RuntimeError):
        audit_mod.configure(None); audit_mod.log_security_event(
            EventType.AUTH_SUCCESS, "x", Severity.INFO, Result.SUCCESS, "r")
    log = AuditLogger(tmp_path / "a.jsonl")
    audit_mod.configure(log)
    try:
        audit_mod.log_security_event(EventType.TLS_CERTIFICATE_REJECTED, "n", Severity.CRITICAL,
                                     Result.REJECTED, "r1")
    finally:
        audit_mod.configure(None); log.close()
    assert len(list(AuditLogger.read_events(tmp_path / "a.jsonl"))) == 1