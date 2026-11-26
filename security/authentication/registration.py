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
    """Identity cache backed by the coordinator security repository when configured."""

    def __init__(self, clock: Callable[[], float] = time.time, repository=None):
        self._clock = clock
        self._repository = repository
        self._by_id: dict[str, Identity] = {}
        if repository is not None:
            self._by_id = {i.identity: i for i in repository.load_identities()}

    def register(self, role: Role | str, label: str,
                 certificate_fingerprint: str | None = None) -> tuple[str, str]:
        """Policy signature: register(role, label) -> (identity, credential)."""
        credential = secrets.token_urlsafe(32)
        ident = Identity(str(uuid.uuid4()), label, Role(role), hash_credential(credential),
                         self._clock())
        if self._repository is not None:
            self._repository.save_identity(ident, certificate_fingerprint)
        self._by_id[ident.identity] = ident
        return ident.identity, credential

    def get(self, identity: str) -> Identity | None:
        return self._by_id.get(identity)

    def rotate_credential(self, identity: str) -> str | None:
        """Issue a fresh credential without changing identity or security history."""
        ident = self._by_id.get(identity)
        if ident is None or ident.revoked:
            return None
        credential = secrets.token_urlsafe(32)
        updated = Identity(ident.identity, ident.label, ident.role,
                           hash_credential(credential), ident.created_at, ident.revoked)
        if self._repository is not None and not self._repository.rotate_credential(
                identity, updated.credential_hash):
            return None
        self._by_id[identity] = updated
        return credential

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
        if self._repository is not None and not self._repository.set_identity_revoked(identity):
            return False
        ident.revoked = True
        return True
