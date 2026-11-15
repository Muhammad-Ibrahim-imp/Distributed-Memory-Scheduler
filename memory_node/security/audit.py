import logging
from typing import Callable

from common.types.security import SecurityEvent, EventType, Severity, Result

logger = logging.getLogger("memory_node.audit")

_LEVELS = {
    Severity.INFO: logging.INFO,
    Severity.WARNING: logging.WARNING,
    Severity.CRITICAL: logging.CRITICAL,
}


class SecurityAuditReporter:
    """Builds SecurityEvents for local violations and hands them on.
    Never pass keys named token, credential, password, secret, payload or data.

    event_sink [PROPOSED to B1]: an optional synchronous callable that receives each
    SecurityEvent, so B1 can forward it to the coordinator (node-local audit logging
    alone never reaches M5). It should return quickly, for example by queuing."""

    def __init__(self, node_id: str,
                 event_sink: Callable[[SecurityEvent], None] | None = None):
        self.node_id = node_id    # replaced by MemoryNode.update_node_identity
        self._event_sink = event_sink

    def report_integrity_failed(self, object_id: str, request_id: str) -> None:
        """SHA-256 mismatch on read (agreed with M5): node as actor, Result.FAILURE,
        CRITICAL, the read's request_id, object_id only. One event moves the node to
        SUSPICIOUS. TAMPER_DETECTED is reserved for B1's token check and must NOT be
        used. Until M5 adds EventType.OBJECT_INTEGRITY_FAILED, the failure is logged
        locally only and not sent."""
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
        changes trust state, and free still succeeds. Report it every time."""
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
        """Two independent routes, so neither failure stops the other: the optional
        sink (B1 forwards to the coordinator), and M5's synchronous
        log_security_event (node-local audit). If that is not importable or
        configured, the event is still logged locally so it is not lost."""
        if self._event_sink is not None:
            try:
                self._event_sink(event)
            except Exception:
                logger.exception("event sink failed")
        try:
            from security.monitoring.audit import log_security_event
            log_security_event(event.event_type, event.actor_id, event.severity,
                               event.result, event.request_id, **event.extra)
        except Exception:
            logger.warning("could not deliver event to the local audit log; "
                           "logging locally", exc_info=True)
            logger.log(_LEVELS[event.severity], "security_event %s", event.to_dict())