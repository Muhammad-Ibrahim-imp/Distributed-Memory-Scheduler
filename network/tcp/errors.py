from common.types.errors import (
    AuthenticationError, DSMError, DSMTimeoutError, IntegrityError,
    InvalidRequestError, NotOwnerError, ObjectNotFoundError,
    ObjectUnavailableError, OutOfCapacityError, QuotaExceededError,
    RateLimitedError, TokenExpiredError, TokenInvalidError,
)

CODE_TO_ERROR: dict[str, type[DSMError]] = {
    "OBJECT_NOT_FOUND": ObjectNotFoundError,
    "ACCESS_DENIED": NotOwnerError,
    "OBJECT_UNAVAILABLE": ObjectUnavailableError,
    "CAPACITY_EXCEEDED": OutOfCapacityError,
    "QUOTA_EXCEEDED": QuotaExceededError,
    "INTEGRITY_FAILURE": IntegrityError,
    "INVALID_REQUEST": InvalidRequestError,
    "TIMEOUT": DSMTimeoutError,
    "AUTH_REJECTED": AuthenticationError,
    "TOKEN_INVALID": TokenInvalidError,
    "TOKEN_EXPIRED": TokenExpiredError,
    "RATE_LIMITED": RateLimitedError,
}
ERROR_TO_CODE: dict[type[DSMError], str] = {v: k for k, v in CODE_TO_ERROR.items()}


def error_to_code(exc: DSMError) -> str:
    for cls in type(exc).__mro__:
        if cls in ERROR_TO_CODE:
            return ERROR_TO_CODE[cls]
    return "INVALID_REQUEST"


def code_to_error(code: str, message: str) -> DSMError:
    return CODE_TO_ERROR.get(code, InvalidRequestError)(message)
