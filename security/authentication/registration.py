"""Identity registration (SECURITY_POLICY §2, §3.2).

NOTE: the enrollment-secret gate (who may register at all) lands with Week-5 node
registration. Until then register() is an internal call, not a network endpoint.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class Role(str, Enum):
    CLIENT = "client"
    NODE = "node"


@dataclass
class Identity:
    identity: str              # UUID: the one value that is also actor_id / node_id elsewhere
    label: str                 # cosmetic; never used for a security decision
    role: Role
    credential_hash: str
    created_at: float
    revoked: bool = False


def hash_credential(credential: str) -> str:
    # Credentials are 256-bit random secrets, not passwords: SHA-256 + constant-time
    # compare is right; bcrypt/argon2 only help against guessing low-entropy secrets.
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


_DUMMY_HASH = hash_credential("dummy-credential-for-the-unknown-identity-path")


class IdentityStore:
    """In-memory (persistence arrives with Week-5 node registration)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._by_id: dict[str, Identity] = {}

    def register(self, role: Role | str, label: str) -> tuple[str, str]:
        """Policy signature: register(role, label) -> (identity, credential)."""
        credential = secrets.token_urlsafe(32)
        ident = Identity(str(uuid.uuid4()), label, Role(role), hash_credential(credential),
                         self._clock())
        self._by_id[ident.identity] = ident
        return ident.identity, credential

    def get(self, identity: str) -> Identity | None:
        return self._by_id.get(identity)

    def verify(self, identity: str, credential: str) -> Identity | None:
        """Unknown id and wrong credential take the same path (one compare either way)."""
        ident = self._by_id.get(identity)
        ok = hmac.compare_digest(ident.credential_hash if ident else _DUMMY_HASH,
                                 hash_credential(credential))
        return ident if (ok and ident is not None) else None

    def mark_revoked(self, identity: str) -> bool:
        ident = self._by_id.get(identity)
        if ident is None:
            return False
        ident.revoked = True
        return True