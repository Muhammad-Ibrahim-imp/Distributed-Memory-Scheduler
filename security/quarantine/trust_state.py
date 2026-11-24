"""Event-driven node trust state, with recovery checked lazily on reads."""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from common.interfaces.security_state import SecurityStateProvider
from common.types.security import (
    EventType,
    Result,
    SecurityEvent,
    SecurityStatus,
    Severity,
    SINGLE_EVENT_ESCALATORS,
    TrustState,
)
from security.config import TrustPolicy
from security.quarantine.revocation import RevocationDispatcher


class TrustStateMachine(SecurityStateProvider):
    def __init__(self, policy: TrustPolicy | None = None, clock: Callable[[], float] = time.time,
                 repository=None):
        self.policy = policy or TrustPolicy()
        self.clock = clock
        self._repository = repository
        self._nodes: set[str] = set()
        self._states: dict[str, TrustState] = {}
        self._last_violation: dict[str, float] = {}
        self._last_event: dict[str, str] = {}
        self._auth_failures: dict[str, deque[float]] = defaultdict(deque)
        self._rate_limits: dict[str, deque[float]] = defaultdict(deque)
        self._revocation = RevocationDispatcher()
        self._state_listeners: list[Callable[[str, TrustState, float], Any]] = []
        self._audit = None
        if repository is not None:
            from security.authentication.registration import Role
            self._nodes = {i.identity for i in repository.load_identities() if i.role is Role.NODE}
            for identity, state, updated_at, reason in repository.load_trust():
                self._states[identity] = state
                self._last_event[identity] = reason
                if state is TrustState.SUSPICIOUS:
                    self._last_violation[identity] = updated_at
            window_start = self.clock() - self.policy.suspicious_trigger.window_seconds
            for identity, event_type, timestamp in repository.load_violations(window_start):
                self._last_violation[identity] = max(
                    timestamp, self._last_violation.get(identity, float("-inf"))
                )
                if event_type == EventType.AUTH_FAILURE.value:
                    self._auth_failures[identity].append(timestamp)
                elif event_type == EventType.RATE_LIMIT_VIOLATION.value:
                    self._rate_limits[identity].append(timestamp)
            for identity in self._nodes:
                self._prune(self._auth_failures[identity], self.policy.suspicious_trigger.window_seconds, self.clock())
                self._prune(self._rate_limits[identity], self.policy.suspicious_trigger.window_seconds, self.clock())
                if identity not in self._states:
                    self._states[identity] = TrustState.TRUSTED
                    repository.save_trust(identity, TrustState.TRUSTED, self.clock(), "node_registered")

    def attach_audit(self, audit) -> None:
        self._audit = audit
        audit.add_listener(self.handle_event)

    def register_revocation_listener(self, callback: Callable[[str, float], Any]) -> None:
        self._revocation.register_revocation_listener(callback)

    def set_force_close_connection(self, callback: Callable[[str], Any]) -> None:
        self._revocation.set_force_close_connection(callback)

    def register_state_listener(self, callback: Callable[[str, TrustState, float], Any]) -> None:
        self._state_listeners.append(callback)

    def get_trust_state(self, node_id: str) -> TrustState:
        state = self._states.get(node_id, TrustState.TRUSTED)
        if state is TrustState.SUSPICIOUS:
            clean_period = self.policy.recovery_clean_period_seconds
            if self.clock() - self._last_violation[node_id] >= clean_period:
                self.transition(node_id, TrustState.TRUSTED, "clean_period_elapsed")
                return TrustState.TRUSTED
        return state

    def get_status(self, node_id: str) -> SecurityStatus:
        state = self.get_trust_state(node_id)
        self._prune(
            self._auth_failures[node_id],
            self.policy.suspicious_trigger.window_seconds,
            self.clock(),
        )
        return SecurityStatus(
            node_id=node_id,
            trust_state=state,
            last_event=self._last_event.get(node_id, ""),
            failure_count=len(self._auth_failures[node_id]),
            timestamp=self.clock(),
        )

    def handle_event(self, event: SecurityEvent) -> None:
        identity = event.actor_id
        if event.event_type is EventType.NODE_REGISTERED:
            self._nodes.add(identity)
            self._states.setdefault(identity, TrustState.TRUSTED)
            self._last_event.setdefault(identity, event.event_type.value)
            if self._repository is not None:
                self._repository.save_trust(identity, self._states[identity], event.timestamp,
                                            self._last_event[identity])
            return
        # Pre-auth IP actors, clients, and unknown identities have no node trust state.
        if identity not in self._nodes or event.event_type is EventType.TRUST_TRANSITION:
            return

        kind = event.event_type
        is_auth_failure = kind is EventType.AUTH_FAILURE
        is_rate_limit = kind is EventType.RATE_LIMIT_VIOLATION
        if not (is_auth_failure or is_rate_limit or kind in SINGLE_EVENT_ESCALATORS):
            return

        now = event.timestamp
        self._last_event[identity] = kind.value
        self._last_violation[identity] = now
        if is_auth_failure:
            failures = self._auth_failures[identity]
            self._prune(failures, self.policy.suspicious_trigger.window_seconds, now)
            failures.append(now)
        if is_rate_limit:
            limits = self._rate_limits[identity]
            self._prune(limits, self.policy.suspicious_trigger.window_seconds, now)
            limits.append(now)
        if self._repository is not None:
            self._repository.add_violation(identity, kind.value, now)

        state = self._states.get(identity, TrustState.TRUSTED)

        if state is TrustState.SUSPICIOUS:
            self.transition(identity, TrustState.QUARANTINED, kind.value)
        elif state is TrustState.QUARANTINED:
            self.transition(identity, TrustState.REVOKED, kind.value)
        elif kind in SINGLE_EVENT_ESCALATORS:
            self.transition(identity, TrustState.SUSPICIOUS, kind.value)
        elif (is_auth_failure and
              len(self._auth_failures[identity]) >= self.policy.suspicious_trigger.failed_auths_per_window):
            self.transition(identity, TrustState.SUSPICIOUS, kind.value)
        elif (is_rate_limit and
              len(self._rate_limits[identity]) >= self.policy.suspicious_trigger.failed_auths_per_window):
            # Policy says repeated rate-limit violations; use the configured auth window/count.
            self.transition(identity, TrustState.SUSPICIOUS, kind.value)

    def transition(self, node_id: str, target: TrustState, reason: str = "manual") -> bool:
        return self._transition(node_id, target, reason)

    def _transition(
        self,
        node_id: str,
        target: TrustState,
        reason: str,
        allow_quarantine_reinstatement: bool = False,
    ) -> bool:
        current = self._states.get(node_id, TrustState.TRUSTED)
        target = TrustState(target)
        if current is target or current is TrustState.REVOKED:
            return False
        allowed = {
            TrustState.TRUSTED: {TrustState.SUSPICIOUS, TrustState.REVOKED},
            TrustState.SUSPICIOUS: {TrustState.TRUSTED, TrustState.QUARANTINED, TrustState.REVOKED},
            TrustState.QUARANTINED: {TrustState.TRUSTED, TrustState.REVOKED},
            TrustState.REVOKED: set(),
        }
        if target not in allowed[current]:
            raise ValueError(f"invalid trust transition: {current.value} -> {target.value}")
        if target is TrustState.TRUSTED and current is TrustState.SUSPICIOUS:
            if self.clock() - self._last_violation[node_id] < self.policy.recovery_clean_period_seconds:
                raise ValueError("SUSPICIOUS requires the full clean period before recovery")
        if (target is TrustState.TRUSTED and current is TrustState.QUARANTINED
                and not allow_quarantine_reinstatement):
            raise ValueError("QUARANTINED requires explicit reinstatement")

        timestamp = self.clock()
        transition_event = None
        if self._audit is not None:
            transition_event = SecurityEvent(
                "evt-" + uuid.uuid4().hex[:12], timestamp, EventType.TRUST_TRANSITION,
                Severity.WARNING, node_id, Result.SUCCESS, "-",
                {"from_state": current.value, "to_state": target.value, "reason": reason},
            )
        if self._repository is not None:
            if transition_event is not None:
                self._repository.save_trust_transition(
                    node_id, target, timestamp, reason, transition_event
                )
            else:
                self._repository.save_trust(node_id, target, timestamp, reason)
        self._states[node_id] = target
        if target is TrustState.SUSPICIOUS:
            self._last_violation[node_id] = self.clock()
        if transition_event is not None:
            self._audit.record_event(transition_event)
        for callback in tuple(self._state_listeners):
            try:
                callback(node_id, target, timestamp)
            except Exception:
                logging.getLogger(__name__).exception("trust-state listener failed")
        if target is TrustState.REVOKED:
            self._revocation.transition(node_id, timestamp)
        return True

    def reinstate(self, node_id: str) -> bool:
        if self._states.get(node_id) is not TrustState.QUARANTINED:
            return False
        self._last_violation[node_id] = self.clock()
        return self._transition(node_id, TrustState.TRUSTED, "manual_reinstatement", True)

    @staticmethod
    def _prune(events: deque[float], window: float, now: float) -> None:
        cutoff = now - window
        while events and events[0] < cutoff:
            events.popleft()
