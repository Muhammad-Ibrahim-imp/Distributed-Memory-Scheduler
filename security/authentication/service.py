"""AuthService: register -> authenticate (credential -> token) -> validate (token -> claims).

Order follows SECURITY_POLICY §9.2 step 3-4: the trust-state check runs BEFORE any
credential or token work, and only REVOKED is a hard stop (SUSPICIOUS / QUARANTINED
continue to full verification).

TrustStateReader contract for unknown ids (clients have no trust state): return TRUSTED.
"""
from __future__ import annotations

from dataclasses import dataclass

from common.types.errors import AuthenticationError, TokenExpiredError
from common.types.security import EventType, Result, TrustState, TrustStateReader
from security.authentication.registration import IdentityStore, Role
from security.authentication.tokens import TokenClaims, TokenService
from security.errors import IdentityBlockedError
from security.monitoring.audit import AuditLogger, token_ref
from security.sessions.manager import SessionManager


@dataclass(frozen=True)
class RegistrationResult:
    identity: str
    label: str
    role: Role
    credential: str            # shown ONCE; only its hash is kept


@dataclass(frozen=True)
class AuthResult:
    token: str
    session_id: str
    identity: str
    role: Role
    expires_at: float


class AuthService:
    def __init__(self, identities: IdentityStore, sessions: SessionManager, tokens: TokenService,
                 audit: AuditLogger, trust: TrustStateReader | None = None):
        self._ids, self._sessions, self._tokens, self._audit = identities, sessions, tokens, audit
        self._trust = trust

    def register(self, role: Role | str, label: str, request_id: str | None = None) -> RegistrationResult:
        identity, credential = self._ids.register(role, label)
        role = Role(role)
        self._audit.emit(EventType.NODE_REGISTERED if role is Role.NODE else EventType.CLIENT_REGISTERED,
                         identity, Result.SUCCESS, request_id, label=label)
        return RegistrationResult(identity, label, role, credential)

    def authenticate(self, identity: str, credential: str, request_id: str | None = None) -> AuthResult:
        self._check_trust(identity, request_id)
        ident = self._ids.verify(identity, credential)
        if ident is None:
            self._audit.emit(EventType.AUTH_FAILURE, identity, Result.REJECTED, request_id,
                             reason="invalid_credentials")
            raise AuthenticationError("invalid credentials")     # same for unknown id / wrong secret
        if ident.revoked:
            self._audit.emit(EventType.AUTHZ_DENIED, identity, Result.REJECTED, request_id,
                             reason="identity_revoked")
            raise IdentityBlockedError("identity revoked")
        session = self._sessions.create(identity, ident.role.value)
        token = self._tokens.issue_token(identity, ident.role.value, session.session_id)
        self._audit.emit(EventType.AUTH_SUCCESS, identity, Result.SUCCESS, request_id)
        self._audit.emit(EventType.TOKEN_ISSUED, identity, Result.SUCCESS, request_id,
                         session_id=session.session_id, token_ref=token_ref(token))
        return AuthResult(token, session.session_id, identity, ident.role, session.expires_at)

    def validate(self, token: str, claimed_identity: str | None = None,
                 request_id: str | None = None) -> TokenClaims:
        if claimed_identity is not None:
            self._check_trust(claimed_identity, request_id)      # before touching the token
        try:
            claims = self._tokens.verify_token(token)
            if claimed_identity is not None and claims.identity != claimed_identity:
                raise AuthenticationError("token not bound to claimed identity")
            self._check_trust(claims.identity, request_id)
            ident = self._ids.get(claims.identity)
            if ident is None or ident.revoked:
                raise IdentityBlockedError("identity revoked")
            self._sessions.validate(claims.session_id, claims.identity)
            return claims
        except IdentityBlockedError:
            raise
        except TokenExpiredError:
            self._audit.emit(EventType.TOKEN_EXPIRED, claimed_identity or "-", Result.REJECTED,
                             request_id, token_ref=token_ref(token))
            raise
        except AuthenticationError as exc:
            self._audit.emit(EventType.AUTH_FAILURE, claimed_identity or "-", Result.REJECTED,
                             request_id, reason=type(exc).__name__, token_ref=token_ref(token))
            raise

    def logout(self, token: str, request_id: str | None = None) -> None:
        claims = self._tokens.verify_token(token)
        self._sessions.revoke(claims.session_id, "logout", request_id)

    def revoke_identity(self, identity: str, reason: str = "revoked") -> int:
        """Terminal. Week-5 revocation.transition() calls this. Returns sessions killed."""
        if not self._ids.mark_revoked(identity):
            return 0
        return self._sessions.revoke_all(identity, reason)

    def _check_trust(self, identity: str, request_id: str | None) -> None:
        if self._trust is not None and self._trust.get_trust_state(identity) is TrustState.REVOKED:
            self._audit.emit(EventType.AUTHZ_DENIED, identity, Result.REJECTED, request_id,
                             reason="trust_state_revoked")
            raise IdentityBlockedError("identity revoked")