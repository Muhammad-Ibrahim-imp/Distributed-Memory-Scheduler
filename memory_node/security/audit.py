from common.types.security import SecurityEvent, EventType, Severity, Result


class SecurityAuditReporter:
    """Builds SecurityEvents for local violations and hands them to M5.
    Never pass keys named token, credential, password, secret, payload or data."""

    def __init__(self, node_id: str):
        self.node_id = node_id

    def report_tamper_detected(self, object_id: str, request_id: str) -> None:
        """SHA-256 mismatch on read. TAMPER_DETECTED escalates trust state,
        so CRITICAL is confirmed by M5."""
        self._send(SecurityEvent.create(
            event_type=EventType.TAMPER_DETECTED,
            actor_id=self.node_id,        # recommendation, see questions
            severity=Severity.CRITICAL,
            result=Result.FAILURE,
            request_id=request_id,
            object_id=object_id,
        ))

    def report_authz_denied(self, actor_id: str, request_id: str,
                            reason: str, object_id: str | None = None) -> None:
        """Isolation or quota breach. reason is "not_owner" or "quota".
        AUTHZ_DENIED does not escalate, so WARNING (recommendation)."""
        extra = {"reason": reason}
        if object_id is not None:
            extra["object_id"] = object_id
        self._send(SecurityEvent.create(
            event_type=EventType.AUTHZ_DENIED,
            actor_id=actor_id,            # recommendation: ctx.identity
            severity=Severity.WARNING,
            result=Result.REJECTED,
            request_id=request_id,
            **extra,
        ))

    def report_secure_delete_failed(self, object_id: str, request_id: str) -> None:
        """Confirmed by M5: WARNING, does not move trust state.
        No escalation logic here. Repeated failures go to M5 as a proposal."""
        self._send(SecurityEvent.create(
            event_type=EventType.SECURE_DELETE_FAILED,
            actor_id=self.node_id,
            severity=Severity.WARNING,
            result=Result.FAILURE,
            request_id=request_id,
            object_id=object_id,
        ))

    def _send(self, event: SecurityEvent) -> None:
        raise NotImplementedError("Delivery to M5's audit logger: interface not yet known.")