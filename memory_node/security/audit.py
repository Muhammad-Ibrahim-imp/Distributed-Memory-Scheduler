import hashlib
from common.types.security import SecurityEvent, EventType, Severity  # [Open] confirm exact names with M5

class SecurityAuditReporter:
    """Reports SecurityEvents to M5. Never put raw tokens/credentials/
    payloads in `extra` — SecurityEvent raises on construction if you try."""

    def __init__(self, node_id: str):
        self.node_id = node_id

    def report_tamper_detected(self, object_id: str, request_id: str) -> None:
        raise NotImplementedError

    def report_authz_denied(self, owner_identity: str, request_id: str) -> None:
        raise NotImplementedError

    def _token_ref(self, token: str) -> str:
        """Short hash for correlation — never log the raw token."""
        return hashlib.sha256(token.encode()).hexdigest()[:12]
