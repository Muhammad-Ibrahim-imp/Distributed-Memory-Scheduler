"""Audit logging (SECURITY_POLICY §10): JSONL, append-only, size-rotated.

`log_security_event` is the single entry point for every reporter (M5, A1, B1):
it builds the SecurityEvent, appends the JSONL line, then hands the event to the
registered listeners. The Week-5 trust-state machine registers itself as a listener,
which is how "log + escalate" stays ONE call (policy §10.4).
"""
from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

from common.types.security import EventType, Result, SecurityEvent, Severity

DEFAULT_SEVERITY: dict[EventType, Severity] = {
    EventType.AUTH_SUCCESS: Severity.INFO,
    EventType.TOKEN_ISSUED: Severity.INFO,
    EventType.TOKEN_REFRESHED: Severity.INFO,
    EventType.TOKEN_EXPIRED: Severity.INFO,
    EventType.SESSION_REVOKED: Severity.INFO,
    EventType.NODE_REGISTERED: Severity.INFO,
    EventType.CLIENT_REGISTERED: Severity.INFO,
    EventType.AUTH_FAILURE: Severity.WARNING,
    EventType.AUTHZ_DENIED: Severity.WARNING,
    EventType.RATE_LIMIT_VIOLATION: Severity.WARNING,
    EventType.ENROLLMENT_FAILED: Severity.WARNING,
    EventType.ENROLLMENT_DENIED: Severity.WARNING,
    EventType.SECURE_DELETE_FAILED: Severity.WARNING,
    EventType.TLS_HANDSHAKE_FAILURE: Severity.INFO,
    EventType.ENVELOPE_MALFORMED: Severity.INFO,
    EventType.TIMESTAMP_OUT_OF_WINDOW: Severity.INFO,
    EventType.CONNECTION_FORCE_CLOSED: Severity.INFO,
    EventType.TRUST_TRANSITION: Severity.WARNING,
    EventType.REPLAY_DETECTED: Severity.CRITICAL,
    EventType.TAMPER_DETECTED: Severity.CRITICAL,
    EventType.TLS_CERTIFICATE_REJECTED: Severity.CRITICAL,
}


def token_ref(token: str) -> str:
    """Correlation handle for a token. Policy §10.3: never log the token itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


class AuditLogger:
    def __init__(self, path: str | Path, max_bytes: int = 5 * 1024 * 1024,
                 backup_count: int = 5, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._listeners: list[Callable[[SecurityEvent], None]] = []
        self._logger = logging.getLogger(f"dsm.audit.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self._handler = logging.handlers.RotatingFileHandler(
            self.path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(self._handler)

    def add_listener(self, callback: Callable[[SecurityEvent], None]) -> None:
        self._listeners.append(callback)

    def log_security_event(self, event_type: EventType, actor_id: str, severity: Severity,
                           result: Result, request_id: str, **extra: Any) -> SecurityEvent:
        # SecurityEvent.__post_init__ rejects forbidden keys (token, credential, payload, ...).
        event = SecurityEvent(
            event_id="evt-" + uuid.uuid4().hex[:12], timestamp=self._clock(),
            event_type=event_type, severity=severity, actor_id=actor_id,
            result=result, request_id=request_id, extra=extra)
        self._logger.info(json.dumps(event.to_dict(), sort_keys=True, default=str))
        for cb in self._listeners:
            try:
                cb(event)
            except Exception:       # a broken listener must not break auditing
                logging.getLogger(__name__).exception("audit listener failed")
        return event

    def emit(self, event_type: EventType, actor_id: str, result: Result,
             request_id: str | None, **extra: Any) -> SecurityEvent:
        """M5-internal shorthand: severity from DEFAULT_SEVERITY."""
        return self.log_security_event(
            event_type, actor_id, DEFAULT_SEVERITY.get(event_type, Severity.INFO),
            result, request_id or "-", **extra)

    def flush(self) -> None:
        self._handler.flush()

    def close(self) -> None:
        self._handler.flush()
        self._logger.removeHandler(self._handler)
        self._handler.close()

    @staticmethod
    def read_events(path: str | Path) -> Iterator[dict[str, Any]]:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


# --- module-level entry point for A1 / B1 (policy §10.4) --------------------------
_default: AuditLogger | None = None


def configure(logger: AuditLogger | None) -> None:
    global _default
    _default = logger


def log_security_event(event_type: EventType, actor_id: str, severity: Severity,
                       result: Result, request_id: str, **extra: Any) -> SecurityEvent:
    if _default is None:
        raise RuntimeError("audit logger not configured; call security.monitoring.audit.configure()")
    return _default.log_security_event(event_type, actor_id, severity, result, request_id, **extra)