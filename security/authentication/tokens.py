"""Fernet session tokens (SECURITY_POLICY §3.3). TTL enforced by Fernet itself.

Payload: {"identity", "role", "session_id", "issued_at"}. The policy lists only
identity + issued_at; session_id is added so a session can be revoked (Week-2 plan item)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

from cryptography.fernet import Fernet, InvalidToken

from common.types.errors import TokenExpiredError, TokenInvalidError


@dataclass(frozen=True)
class TokenClaims:
    identity: str
    role: str
    session_id: str
    issued_at: int


class TokenService:
    def __init__(self, key: str | bytes | None, ttl_seconds: int = 300,
                 clock: Callable[[], float] = time.time):
        self.ephemeral = key is None     # dev only: tokens die with the process
        self._fernet = Fernet(key if key is not None else Fernet.generate_key())
        self.ttl_seconds = ttl_seconds
        self._clock = clock

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("ascii")

    def issue_token(self, identity: str, role: str, session_id: str) -> str:
        now = int(self._clock())
        payload = json.dumps({"identity": identity, "role": role,
                              "session_id": session_id, "issued_at": now})
        return self._fernet.encrypt_at_time(payload.encode("utf-8"), now).decode("ascii")

    def verify_token(self, token: str) -> TokenClaims:
        try:
            raw = token.encode("ascii") if isinstance(token, str) else token
            data = self._fernet.decrypt_at_time(raw, self.ttl_seconds, int(self._clock()))
        except InvalidToken:
            # authentic-but-old vs forged/garbled: only the first is TOKEN_EXPIRED
            try:
                self._fernet.decrypt(raw)
            except InvalidToken:
                raise TokenInvalidError("token invalid") from None
            raise TokenExpiredError("token expired") from None
        except (UnicodeError, ValueError):
            raise TokenInvalidError("token invalid") from None
        try:
            o = json.loads(data)
            return TokenClaims(o["identity"], o["role"], o["session_id"], int(o["issued_at"]))
        except (KeyError, ValueError, TypeError):
            raise TokenInvalidError("token payload invalid") from None