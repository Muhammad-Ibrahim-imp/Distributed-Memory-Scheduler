import logging

from common.types.security import SecurityEvent, EventType, Severity, Result

logger = logging.getLogger("memory_node.audit")

_LEVELS = {
    Severity.INFO: logging.INFO,
    Severity.WARNING: logging.WARNING,
    Severity.CRITICAL: logging.CRITICAL,
}


class SecurityAuditReporter:
    """Builds SecurityEvents for local violations and hands them to M5.
    Never pass keys named token, credential, password, secret, payload or data."""

    def __init__(self, node_id: str):
        self.node_id = node_id   # M5: the node identity and node_id are the same string

    def report_integrity_failed(self, object_id: str, request_id: str) -> None:
        """SHA-256 mismatch on read: node as actor, Result.FAILURE, CRITICAL (M5).
        TAMPER_DETECTED must NOT be used here. It is reserved for B1's token check and
        escalates trust state. [PROPOSED to M5] EventType.OBJECT_INTEGRITY_FAILED.
        Until M5 adds it, the failure is logged locally only and not sent."""
        event_type = getattr(EventType, "OBJECT_INTEGRITY_FAILED", None)
        if event_type is None:
            logger.critical("object integrity failure (no shared event type yet): "
                            "object_id=%s request_id=%s", object_id, request_id)
            return
        self._send(SecurityEvent.create(
            event_type=event_type,
            actor_id=self.node_id,
            severity=Severity.CRITICAL,
            result=Result.FAILURE,
            request_id=request_id,
            object_id=object_id,
        ))

    def report_authz_denied(self, actor_id: str, request_id: str,
                            reason: str, object_id: str | None = None) -> None:
        """Isolation or quota breach. reason is "not_owner" or "quota". Confirmed by M5:
        AUTHZ_DENIED, requester as actor, Result.REJECTED. Severity WARNING is mine."""
        extra = {"reason": reason}
        if object_id is not None:
            extra["object_id"] = object_id
        self._send(SecurityEvent.create(
            event_type=EventType.AUTHZ_DENIED,
            actor_id=actor_id,
            severity=Severity.WARNING,
            result=Result.REJECTED,
            request_id=request_id,
            **extra,
        ))

    def report_secure_delete_failed(self, object_id: str, request_id: str,
                                    owner_identity: str | None = None) -> None:
        """Confirmed by M5: WARNING, Result.FAILURE, actor_id is the node, never
        escalates, and free still succeeds. Report it every time it happens."""
        extra = {"object_id": object_id}
        if owner_identity is not None:
            extra["owner"] = owner_identity
        self._send(SecurityEvent.create(
            event_type=EventType.SECURE_DELETE_FAILED,
            actor_id=self.node_id,
            severity=Severity.WARNING,
            result=Result.FAILURE,
            request_id=request_id,
            **extra,
        ))

    def _send(self, event: SecurityEvent) -> None:
        """Delivers to M5's synchronous log_security_event. If it is not importable or
        not configured (security.bootstrap), the event is still logged locally so it
        is not lost."""
        try:
            from security.monitoring.audit import log_security_event
            log_security_event(event.event_type, event.actor_id, event.severity,
                               event.result, event.request_id, **event.extra)
        except Exception:
            logger.warning("could not deliver event to M5; logging locally",
                           exc_info=True)
            logger.log(_LEVELS[event.severity], "security_event %s", event.to_dict())