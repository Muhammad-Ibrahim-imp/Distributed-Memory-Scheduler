"""M5's security API: the in-process hooks B1, A1, A2 and B2 call.

Signatures are pinned here so every caller can write a fake against the same
shape before the real modules land. Implementations live where the policy
names them (SECURITY_POLICY.md §3.6, §6.7, §10.4):

    log_security_event  -> security/monitoring/audit.py
    get_trust_state     -> security/quarantine/trust_state.py
    verify_token        -> security/authentication/tokens.py

All three are SYNCHRONOUS. They run inside the coordinator process -- the same
process as the DSM Runtime and the Scheduler (§12) -- so there is no I/O wait
worth an `await`. `verify_token` is the one exception in COST, not in shape:
Fernet decryption is CPU-bound. It only runs for identities that already
survived the trust-state check at pipeline step 3 (§9.2), so a REVOKED
identity never reaches it.

`get_trust_state` is inherited from TrustStateReader rather than redeclared,
so there is exactly one definition of that method in the codebase.
"""

from typing import Any, Protocol, runtime_checkable

from common.types.security import (
    EventType,
    Result,
    SecurityEvent,
    Severity,
    TrustStateReader,
)


@runtime_checkable
class SecurityAPI(TrustStateReader, Protocol):
    """What M5 offers to the rest of the system. One fake implements all three.

    Runtime-checkable so a fake can assert conformance in a test --
    `assert isinstance(fake, SecurityAPI)`. Note it checks method NAMES only,
    not signatures, so it catches a missing method and not a wrong one.
    """

    def log_security_event(
        self,
        event_type: EventType,
        actor_id: str,
        severity: Severity,
        result: Result,
        request_id: str,
        **extra: Any,
    ) -> SecurityEvent:
        """Record one audit entry AND feed trust-state escalation (§6.4).

        One call, not two. Reporters never call an escalation function
        separately: if logging and escalating were two calls, forgetting the
        second is a silent security hole while forgetting the first is only a
        missing log line, and only the hole surfaces in an incident.

        `actor_id` is the identity the event concerns -- the same string as
        `identity` in the envelope and `node_id` in SecurityStatus (§10.4).
        The escalation table keys on it, so a divergence updates the wrong
        record or none. Routine events (`auth_success`, `token_refreshed`)
        flow through and change nothing.

        Raises ValueError if `extra` contains a key in FORBIDDEN_EXTRA_KEYS
        (token, credential, password, secret, payload, data) -- that is a
        programming error, not a runtime condition.
        """
        ...

    def verify_token(self, token: str) -> str:
        """Return the identity the token belongs to.

        THIS IS WHERE TAMPER DETECTION HAPPENS (§3.6, §6.6). `cryptography`'s
        Fernet raises the same `InvalidToken` for a bad HMAC and for an
        expired TTL, so this function separates them:

          * integrity check failed  -> raises TokenInvalidError
          * genuine but past its TTL -> raises TokenExpiredError (retryable)

        Those two must stay distinguishable end to end: callers map them to
        the TOKEN_INVALID and TOKEN_EXPIRED wire codes, and B1 reports
        TAMPER_DETECTED for the first, which is a trust-state escalation,
        while expiry is routine and is not (§6.6).

        Deliberately takes the raw token and nothing else. It does not take an
        identity: the token is the claim, and returning the identity is the
        point. Taking an identity as well would invite callers to trust the
        one they passed in.
        """
        ...
