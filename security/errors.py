"""Security errors. The wire-level ones (AuthenticationError, TokenInvalidError,
TokenExpiredError) already live in common/types/errors.py; re-exported here so
security code has one import. Anything new subclasses AuthenticationError and
therefore maps to the AUTH_REJECTED wire code."""
from common.types.errors import AuthenticationError, TokenExpiredError, TokenInvalidError

__all__ = ["AuthenticationError", "TokenExpiredError", "TokenInvalidError",
           "SessionRevokedError", "IdentityBlockedError"]


class SessionRevokedError(AuthenticationError):
    """The session behind a (still cryptographically valid) token was revoked."""


class IdentityBlockedError(AuthenticationError):
    """Identity is REVOKED (trust state or explicit revocation); refused outright."""