"""Shared error types for the DSM API and the security pipeline.

Every error inherits from DSMError, so a caller can catch DSMError once per
object (for example in a cleanup loop) without one failure aborting the rest.

`retryable` says whether repeating the same call can succeed:
    True  -> transient (timeout, rate limit, expired token)
    False -> repeating will not help
"""


class DSMError(Exception):
    """Base class for every error raised through the DSM API."""

    retryable: bool = False


# --- Raised by A2's runtime / Object Manager --------------------------------

class ObjectNotFoundError(DSMError):
    """The object id does not exist."""


class NotOwnerError(DSMError):
    """The caller does not own the object (raised when check_access returns False)."""


class ObjectUnavailableError(DSMError):
    """The object's node is REVOKED, or a write targets an object on a
    QUARANTINED node. Retrying will not help."""


class OutOfCapacityError(DSMError):
    """No node has enough free capacity for the allocation."""


class InvalidRequestError(DSMError):
    """The request is malformed (bad size, bad id, ...)."""


class DSMTimeoutError(DSMError):
    """The operation timed out. Safe to retry."""

    retryable = True


class IntegrityError(DSMError):
    """Stored data failed its SHA-256 integrity check."""


# --- Raised by the security pipeline (M5 / B1) ------------------------------
# Wire codes: AUTH_REJECTED, TOKEN_INVALID, TOKEN_EXPIRED, RATE_LIMITED.
# Replay and tamper rejections surface as AuthenticationError unless B1 asks
# for dedicated codes.

class AuthenticationError(DSMError):
    """AUTH_REJECTED: the request failed authentication."""


class TokenInvalidError(AuthenticationError):
    """TOKEN_INVALID: the token is malformed or has been tampered with."""


class TokenExpiredError(AuthenticationError):
    """TOKEN_EXPIRED: refresh the token with the credential, then retry.
    Normally handled below the DSM API, so adapters rarely see it."""

    retryable = True


class RateLimitedError(DSMError):
    """RATE_LIMITED: too many requests. Back off, then retry."""

    retryable = True