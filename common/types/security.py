"""Shared security types: trust state, node security status, and audit events.

Defined by M5 (SECURITY_POLICY.md §6, §7.1, §10). Everyone imports these, and
B1 mirrors SecurityEvent in its Protobuf definition.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class TrustState(str, Enum):
    TRUSTED = "TRUSTED"          # eligible for placement
    SUSPICIOUS = "SUSPICIOUS"    # no new placement
    QUARANTINED = "QUARANTINED"  # reads only, no new placement
    REVOKED = "REVOKED"          # excluded, connection refused


@dataclass(frozen=True)
class SecurityStatus:
    """What M5 maintains per node and B2 reads (policy §7.1).

    A1 and B1 report security *events* into M5; they do not report status.
    """

    node_id: str
    trust_state: TrustState
    last_event: str
    failure_count: int
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "trust_state": self.trust_state.value,
            "last_event": self.last_event,
            "failure_count": self.failure_count,
            "timestamp": self.timestamp,
        }


class TrustStateReader(Protocol):
    """Implemented by M5 (security/quarantine/trust_state.py).
    Called by B2 at every placement decision and by A2 before writes to
    existing objects. Synchronous: a dict lookup plus the lazy recovery check.
    """

    def get_trust_state(self, node_id: str) -> TrustState: ...


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Result(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    REJECTED = "rejected"


class EventType(str, Enum):
    # Policy §10.1. A1 and B1 add the types they report (integrity failures,
    # TLS failures, ...) here once they propose them.
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"
    AUTHZ_DENIED = "authz_denied"
    TOKEN_ISSUED = "token_issued"
    TOKEN_REFRESHED = "token_refreshed"
    TOKEN_EXPIRED = "token_expired"
    TRUST_TRANSITION = "trust_transition"
    RATE_LIMIT_VIOLATION = "rate_limit_violation"
    REPLAY_DETECTED = "replay_detected"
    TAMPER_DETECTED = "tamper_detected"
    NODE_REGISTERED = "node_registered"
    CLIENT_REGISTERED = "client_registered"
    ENROLLMENT_FAILED = "enrollment_failed"
    ENROLLMENT_DENIED = "enrollment_denied"
    # Transport-layer events, reported by B1 (policy §9.2 steps 1, 2, 5).
    # Only TLS_CERTIFICATE_REJECTED escalates -- see SINGLE_EVENT_ESCALATORS.
    TLS_HANDSHAKE_FAILURE = "tls_handshake_failure"
    TLS_CERTIFICATE_REJECTED = "tls_certificate_rejected"
    ENVELOPE_MALFORMED = "envelope_malformed"
    TIMESTAMP_OUT_OF_WINDOW = "timestamp_out_of_window"
    CONNECTION_FORCE_CLOSED = "connection_force_closed"
    # Node-local events, reported by A1 (policy §10.1). A failed secure delete
    # does NOT escalate -- see SINGLE_EVENT_ESCALATORS.
    SECURE_DELETE_FAILED = "secure_delete_failed"


# Policy §6.4: one event of one of these types alone moves TRUSTED -> SUSPICIOUS.
#
# AUTH_FAILURE and RATE_LIMIT_VIOLATION are deliberately absent: they escalate
# only on repetition (3 failed auths / 60s), so that threshold counting lives in
# security/quarantine/trust_state.py, not in a flat set of types.
#
# TIMESTAMP_OUT_OF_WINDOW is absent for a different reason: a timestamp outside
# the window is usually clock skew, not an attack. Escalating on it would let a
# node with an un-synced clock quarantine itself. Only actual nonce reuse
# (REPLAY_DETECTED) escalates. Same reasoning excludes TLS_HANDSHAKE_FAILURE
# and ENVELOPE_MALFORMED -- both are routine on a lossy network or mid-
# integration, and TLS_CERTIFICATE_REJECTED is the one that means someone
# presented a credential they should not have had.
#
# SECURE_DELETE_FAILED is absent too, but it is the uncomfortable one: a
# failed wipe means data that should be gone may still be readable. That is a
# real exposure, so it logs at WARNING -- but it is an operational failure, not
# evidence the node turned hostile, so it does not move trust state. Severity
# and escalation are separate axes: loud in the log, no change to the node.
# If A1 ever sees a node fail wipes *repeatedly*, that is a different signal
# and should come back to M5 as a proposal rather than being inferred here.
SINGLE_EVENT_ESCALATORS = frozenset(
    {
        EventType.REPLAY_DETECTED,
        EventType.TAMPER_DETECTED,
        EventType.TLS_CERTIFICATE_REJECTED,
    }
)


# Policy §10.3: these must never appear in a log entry. Log `token_ref`
# (a short hash) if you need to correlate entries.
FORBIDDEN_EXTRA_KEYS = frozenset(
    {"token", "credential", "password", "secret", "payload", "data"}
)


@dataclass(frozen=True)
class SecurityEvent:
    event_id: str
    timestamp: float
    event_type: EventType
    severity: Severity
    actor_id: str
    result: Result
    request_id: str
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        bad = FORBIDDEN_EXTRA_KEYS & {str(k).lower() for k in self.extra}
        if bad:
            raise ValueError(f"never log these in an audit event: {sorted(bad)}")

    @classmethod
    def create(
        cls,
        event_type: EventType,
        actor_id: str,
        severity: Severity,
        result: Result,
        request_id: str,
        **extra: Any,
    ) -> "SecurityEvent":
        """Build an event with a fresh event_id and the current time."""
        return cls(
            event_id="evt-" + uuid.uuid4().hex[:12],
            timestamp=time.time(),
            event_type=event_type,
            severity=severity,
            actor_id=actor_id,
            result=result,
            request_id=request_id,
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        """One JSON-serializable dict, ready to be written as a JSONL line."""
        d: dict[str, Any] = {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "actor_id": self.actor_id,
            "result": self.result.value,
            "request_id": self.request_id,
        }
        if self.extra:
            d["extra"] = self.extra
        return d