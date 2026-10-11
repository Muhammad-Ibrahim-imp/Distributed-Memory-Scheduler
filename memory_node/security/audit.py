import hashlib
from common.types.security import SecurityEvent, EventType, Severity   # verify names after pull


class SecurityAuditReporter:
    """Reports SecurityEvents to M5. Never put token, credential, password,
    secret, payload or data in `extra`; SecurityEvent raises if you do."""

    def __init__(self, node_id: str):
        self.node_id = node_id

    def report_tamper_detected(self, object_id: str, request_id: str) -> None:
        raise NotImplementedError
        # EventType.TAMPER_DETECTED, Severity.CRITICAL, extra={"object_id": object_id}

    def report_authz_denied(self, owner_identity: str, request_id: str,
                            reason: str) -> None:
        raise NotImplementedError
        # EventType.AUTHZ_DENIED; reason is "not_owner" or "quota".
        # Recommendation: lets the audit log tell the two apart.

    def report_secure_delete_failed(self, object_id: str, request_id: str) -> None:
        raise NotImplementedError
        # EventType.SECURE_DELETE_FAILED, WARNING level, does NOT move trust state.
        # Do not add escalation logic. If a node fails wipes repeatedly,
        # bring M5 a proposal.

    def _token_ref(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()[:12]