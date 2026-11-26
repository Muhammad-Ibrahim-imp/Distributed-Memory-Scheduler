"""Gate registration and keep revoked node certificates off the coordinator."""
from __future__ import annotations

import hmac
import json
import os
import re
import time
from pathlib import Path
from typing import Mapping

from security.config import EnrollmentPolicy
from security.authentication.registration import Role

_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_MIN_SECRET_LENGTH = 32


class EnrollmentDenied(Exception):
    """The role secret or node certificate is not authorized to enroll."""


class EnrollmentGuard:
    def __init__(self, policy: EnrollmentPolicy, environ: Mapping[str, str] | None = None,
                 repository=None):
        environ = os.environ if environ is None else environ
        self._secrets = {
            Role.NODE: environ.get(policy.node_secret_env, ""),
            Role.CLIENT: environ.get(policy.client_secret_env, ""),
        }
        invalid = [role.value for role, secret in self._secrets.items()
                   if len(secret.encode("utf-8")) < _MIN_SECRET_LENGTH]
        if invalid:
            raise ValueError(
                "enrollment secrets must be configured and at least 32 bytes for: "
                + ", ".join(invalid)
            )

        self._denylist_path = Path(policy.denylist_path)
        self._repository = repository
        self._denied_fingerprints = self._load_denylist()
        if repository is not None:
            for fingerprint in self._denied_fingerprints:
                repository.add_ban(fingerprint, "node_certificate", time.time(), "legacy_denylist")
        self._fingerprints_by_identity: dict[str, str] = {}

    def authorize(self, role: Role | str, secret: str, cert_fingerprint: str,
                  label: str | None = None) -> Role:
        try:
            role = Role(role)
        except ValueError as exc:
            raise EnrollmentDenied from exc
        expected = self._secrets[role]
        if not secret or not hmac.compare_digest(secret, expected):
            raise EnrollmentDenied
        if role is Role.NODE:
            fingerprint = self._normalize_fingerprint(cert_fingerprint)
            if (fingerprint in self._denied_fingerprints or
                    (self._repository is not None and self._repository.has_ban(fingerprint, "node_certificate"))):
                raise EnrollmentDenied
            if (self._repository is not None and
                    self._repository.get_node_identity_for_certificate(fingerprint) is not None):
                raise EnrollmentDenied
            if (self._repository is not None and label and
                    self._repository.get_node_identity_for_label(label) is not None):
                raise EnrollmentDenied
        return role

    def bind_node(self, identity: str, cert_fingerprint: str) -> None:
        fingerprint = self._normalize_fingerprint(cert_fingerprint)
        if self._repository is not None and self._repository.get_node_certificate(identity) != fingerprint:
            raise EnrollmentDenied
        self._fingerprints_by_identity[identity] = fingerprint

    def authorize_node_reconnect(self, identity: str, cert_fingerprint: str) -> None:
        """Reject banned or changed node certificates during credential refresh."""
        fingerprint = self._normalize_fingerprint(cert_fingerprint)
        if (fingerprint in self._denied_fingerprints or
                (self._repository is not None and
                 self._repository.has_ban(fingerprint, "node_certificate"))):
            raise EnrollmentDenied
        bound = (self._repository.get_node_certificate(identity) if self._repository is not None
                 else self._fingerprints_by_identity.get(identity))
        if bound is None or not hmac.compare_digest(bound, fingerprint):
            raise EnrollmentDenied
        self._fingerprints_by_identity[identity] = fingerprint

    def identity_for_reenrollment(self, secret: str, cert_fingerprint: str,
                                  label: str | None = None) -> str | None:
        """Resolve a returning node only from its verified certificate and factor."""
        expected = self._secrets[Role.NODE]
        if not secret or not hmac.compare_digest(secret, expected):
            raise EnrollmentDenied
        fingerprint = self._normalize_fingerprint(cert_fingerprint)
        if (fingerprint in self._denied_fingerprints or
                (self._repository is not None and
                 self._repository.has_ban(fingerprint, "node_certificate"))):
            raise EnrollmentDenied
        identity = (self._repository.get_node_identity_for_certificate(fingerprint)
                    if self._repository is not None else
                    next((key for key, value in self._fingerprints_by_identity.items()
                          if hmac.compare_digest(value, fingerprint)), None))
        if identity is None:
            return None
        # A label may detect operator misconfiguration, but never authenticates identity.
        return identity

    def record_revocation(self, identity: str, _timestamp: float) -> None:
        fingerprint = (self._repository.get_node_certificate(identity) if self._repository is not None
                       else self._fingerprints_by_identity.get(identity))
        if fingerprint is None or fingerprint in self._denied_fingerprints:
            return
        denied = self._denied_fingerprints | {fingerprint}
        self._denylist_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._denylist_path.with_name(self._denylist_path.name + ".tmp")
        try:
            temporary.write_text(json.dumps(sorted(denied), indent=2) + "\n", encoding="utf-8")
            temporary.replace(self._denylist_path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
        self._denied_fingerprints = denied
        if self._repository is not None:
            self._repository.add_ban(fingerprint, "node_certificate", _timestamp,
                                     "node_revoked", identity)

    def _load_denylist(self) -> set[str]:
        try:
            values = json.loads(self._denylist_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return set()
        if not isinstance(values, list):
            raise ValueError(f"invalid node certificate denylist: {self._denylist_path}")
        try:
            return {self._normalize_fingerprint(value) for value in values}
        except (TypeError, EnrollmentDenied) as exc:
            raise ValueError(f"invalid node certificate denylist: {self._denylist_path}") from exc

    @staticmethod
    def _normalize_fingerprint(fingerprint: str) -> str:
        value = fingerprint.lower() if isinstance(fingerprint, str) else ""
        if not _FINGERPRINT.fullmatch(value):
            raise EnrollmentDenied
        return value
