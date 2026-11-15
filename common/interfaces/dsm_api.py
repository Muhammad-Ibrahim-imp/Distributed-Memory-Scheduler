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

Trust state (policy §6.3) -- what the runtime checks before each call:
  TRUSTED / SUSPICIOUS   read, write and free all allowed
  QUARANTINED            read and free allowed; write raises
                         ObjectUnavailableError
  REVOKED                all raise ObjectUnavailableError; the connection is
                         force-closed first, so calls usually fail at the
                         transport layer instead
  Placement is B2's decision -- the runtime never chooses a node. The runtime
  takes the trust-state reader as an injected dependency and calls
  get_trust_state(node_id) duck-typed, so it is not bound to M5's
  TrustStateReader or B2's SecurityStateProvider while that is being settled.
"""

from typing import Protocol


class DSMAPI(Protocol):
    async def alloc(self, size: int) -> str:
        """Reserve `size` bytes and return a new object_id.

        `size` must be > 0. There is no separate "grow": an object is fixed
        at the size it was allocated.

        Raises: InvalidRequestError, OutOfCapacityError (the selected node is
        full), QuotaExceededError (THIS client exceeded its quota on the
        selected node -- policy §5), DSMTimeoutError, plus pipeline errors
        (AuthenticationError, RateLimitedError).
        """
        ...

    async def read(self, object_id: str) -> bytes:
        """Return the bytes last written to the object, or b"" if never written.

        Reads are full-value replacements, not fixed-size zero-padded buffers.
        A1 initializes unwritten objects as empty and isolates each allocation,
        so no previous owner's bytes can be exposed.

        Raises: ObjectNotFoundError, NotOwnerError, IntegrityError,
        DSMTimeoutError, plus pipeline errors. ObjectUnavailableError when the
        object's node has failed or is REVOKED. QUARANTINED nodes remain
        readable so data isn't stranded (policy §6.3, §7.4).
        """
        ...

    async def write(self, object_id: str, data: bytes) -> None:
        """Replace the object's complete value with `data`.

        `data` may be shorter than the allocated size, but it replaces the
        entire previous value. It may NOT be longer -- an object does not grow,
        so that is InvalidRequestError, not OutOfCapacityError.

        Raises: ObjectNotFoundError, NotOwnerError, ObjectUnavailableError
        (node failed, REVOKED, or QUARANTINED -- writes are blocked in all),
        InvalidRequestError (len(data) > allocated size), DSMTimeoutError,
        plus pipeline errors.
        """
        ...

    async def free(self, object_id: str) -> None:
        """Release the object. Idempotent: an allocated-but-never-written id
        frees normally, and an unknown or already-freed id is a silent no-op.

        Allowed on a QUARANTINED node -- `free` is a mutation, but it REDUCES
        exposure rather than increasing it, and refusing it would strand the
        client's allocations and its quota forever with no way to clean up.
        Policy §6.3's "reads only" blocks content writes, not release.

        On REVOKED the remote node is not contacted; local metadata is dropped
        because cleanup cannot be performed through the revoked connection.

        Raises only: NotOwnerError, ObjectUnavailableError, InvalidRequestError,
        or pipeline errors.
        """
        ...
