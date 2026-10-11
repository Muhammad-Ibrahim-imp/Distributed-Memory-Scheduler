"""Session records. Expiry of the token itself is authoritative (Fernet TTL); the
record exists so a session can be REVOKED and is bound to exactly one identity."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from common.types.errors import TokenExpiredError, TokenInvalidError
from common.types.security import EventType, Result
from security.errors import SessionRevokedError
from security.monitoring.audit import AuditLogger


@dataclass
class Session:
    session_id: str
    identity: str
    role: str
    created_at: float
    expires_at: float
    revoked: bool = False
    revoked_reason: str | None = None


class SessionManager:
    def __init__(self, ttl_seconds: int = 300, max_per_identity: int = 5,
                 clock: Callable[[], float] = time.time, audit: AuditLogger | None = None):
        self.ttl_seconds, self.max_per_identity = ttl_seconds, max_per_identity
        self._clock, self._audit = clock, audit
        self._sessions: dict[str, Session] = {}

    def _live(self, s: Session) -> bool:
        return not s.revoked and self._clock() < s.expires_at

    def create(self, identity: str, role: str) -> Session:
        active = sorted((s for s in self._sessions.values()
                         if s.identity == identity and self._live(s)), key=lambda s: s.created_at)
        while len(active) >= self.max_per_identity:      # evict oldest
            self.revoke(active.pop(0).session_id, "max_sessions_exceeded")
        now = self._clock()
        s = Session(uuid.uuid4().hex, identity, role, now, now + self.ttl_seconds)
        self._sessions[s.session_id] = s
        return s

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def validate(self, session_id: str, identity: str) -> Session:
        s = self._sessions.get(session_id)
        if s is None or s.identity != identity:
            raise TokenInvalidError("unknown session")
        if s.revoked:
            raise SessionRevokedError("session revoked")
        if self._clock() >= s.expires_at:
            raise TokenExpiredError("session expired")
        return s

    def revoke(self, session_id: str, reason: str = "revoked", request_id: str | None = None) -> bool:
        s = self._sessions.get(session_id)
        if s is None or s.revoked:
            return False
        s.revoked, s.revoked_reason = True, reason
        if self._audit:
            self._audit.emit(EventType.SESSION_REVOKED, s.identity, Result.SUCCESS, request_id,
                             session_id=s.session_id, reason=reason)
        return True

    def revoke_all(self, identity: str, reason: str = "revoked") -> int:
        ids = [s.session_id for s in self._sessions.values() if s.identity == identity and not s.revoked]
        return sum(self.revoke(i, reason) for i in ids)

    def active_count(self, identity: str) -> int:
        return sum(1 for s in self._sessions.values() if s.identity == identity and self._live(s))

    def sweep(self) -> int:
        now = self._clock()
        dead = [k for k, s in self._sessions.items() if now >= s.expires_at]
        for k in dead:
            del self._sessions[k]
        return len(dead)

    async def sweep_loop(self, interval: float, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.sweep()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass