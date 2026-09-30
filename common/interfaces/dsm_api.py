"""The generic DSM API that application adapters depend on.

A2's runtime implements this. M5 depends only on the shape below, never on
DSM internals.

Agreed with A2:
  * All four methods are `async def`.
  * `object_id` is a plain string holding a UUID4, created by A2's Object
    Manager inside `alloc`. Callers treat it as an opaque handle.
  * An instance is bound to ONE client identity when it is created (identity
    and token are handled below this API), so no method takes an identity.
  * Errors are defined in common/types/errors.py and all inherit DSMError.
"""

from typing import Protocol


class DSMAPI(Protocol):
    async def alloc(self, size: int) -> str:
        """Reserve `size` bytes and return a new object_id.

        Raises: InvalidRequestError, OutOfCapacityError, DSMTimeoutError,
        plus pipeline errors (AuthenticationError, RateLimitedError).
        """
        ...

    async def read(self, object_id: str) -> bytes:
        """Return the object's bytes.

        Raises: ObjectNotFoundError, NotOwnerError, ObjectUnavailableError,
        IntegrityError, DSMTimeoutError, plus pipeline errors.
        """
        ...

    async def write(self, object_id: str, data: bytes) -> None:
        """Store `data` in the object.

        Raises: ObjectNotFoundError, NotOwnerError, ObjectUnavailableError
        (also for a write to a QUARANTINED node), OutOfCapacityError,
        InvalidRequestError, DSMTimeoutError, plus pipeline errors.
        """
        ...

    async def free(self, object_id: str) -> None:
        """Release the object. Idempotent: an allocated-but-never-written id
        frees normally, and an unknown or already-freed id is a silent no-op.
        The owner's free still works on an UNAVAILABLE object.

        Raises only: NotOwnerError, InvalidRequestError, or pipeline errors.
        """
        ...