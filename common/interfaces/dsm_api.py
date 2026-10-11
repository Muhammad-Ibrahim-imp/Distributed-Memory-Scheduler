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

        Raises: InvalidRequestError, OutOfCapacityError (the cluster is full),
        QuotaExceededError (the cluster has room but THIS client is over its
        own cap -- policy §5), DSMTimeoutError, plus pipeline errors
        (AuthenticationError, RateLimitedError).
        """
        ...

    async def read(self, object_id: str) -> bytes:
        """Return the object's bytes. Always exactly the allocated size.

        An object that was allocated but never written reads as `size` ZERO
        bytes -- never uninitialized memory. This is a security requirement,
        not a convenience: handing back a stale buffer would leak whatever
        this node held previously to an unrelated client. A1 owns object
        isolation; this is the API half of the same promise.

        Raises: ObjectNotFoundError, NotOwnerError, IntegrityError,
        DSMTimeoutError, plus pipeline errors. ObjectUnavailableError only
        when the object's node is REVOKED -- on QUARANTINED, reads stay open
        so data isn't stranded (policy §6.3, §7.4).
        """
        ...

    async def write(self, object_id: str, data: bytes) -> None:
        """Store `data` in the object.

        `data` may be SHORTER than the allocated size: the first len(data)
        bytes are replaced and the remainder keeps whatever it held before
        (zeros, if never written). It may NOT be LONGER -- an object does not
        grow, so that is InvalidRequestError, not OutOfCapacityError.

        Raises: ObjectNotFoundError, NotOwnerError, ObjectUnavailableError
        (REVOKED, or QUARANTINED -- writes are blocked in both),
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

        On REVOKED this raises ObjectUnavailableError if it is reached at all,
        but in practice the connection was already force-closed (§7.2), so the
        call fails at the transport layer first.

        Raises only: NotOwnerError, ObjectUnavailableError (REVOKED),
        InvalidRequestError, or pipeline errors.
        """
        ...