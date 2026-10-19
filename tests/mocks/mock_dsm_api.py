"""In-memory fake of the DSM API for adapter tests.

Follows common/interfaces/dsm_api.py:
  * alloc(size) reserves `size` zero bytes; an unwritten object reads as zeros.
  * write() may be SHORTER than the object (the rest keeps its old bytes) but
    never longer (InvalidRequestError).
  * free() is idempotent.

Test helpers:
    calls      list of ("alloc", size) / ("read", oid) / ("write", oid) / ("free", oid)
    live_ids   ids allocated and not yet freed (assert it is empty after cleanup)
    inject()   make the next matching call raise a given error (one shot)
"""

import uuid

from common.types.errors import InvalidRequestError, ObjectNotFoundError


class MockDSMAPI:
    def __init__(self):
        self._store: dict[str, bytes] = {}
        self.calls: list[tuple] = []
        self._faults: list[tuple[str, str | None, Exception]] = []

    @property
    def live_ids(self) -> set[str]:
        return set(self._store)

    def inject(self, op: str, exc: Exception, object_id: str | None = None) -> None:
        """The next `op` call (optionally for one object_id) raises `exc`."""
        self._faults.append((op, object_id, exc))

    def _maybe_fail(self, op: str, object_id: str | None) -> None:
        for i, (o, oid, exc) in enumerate(self._faults):
            if o == op and (oid is None or oid == object_id):
                del self._faults[i]
                raise exc

    async def alloc(self, size: int) -> str:
        self.calls.append(("alloc", size))
        self._maybe_fail("alloc", None)
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise InvalidRequestError(f"bad size: {size!r}")
        oid = str(uuid.uuid4())
        self._store[oid] = bytes(size)
        return oid

    async def read(self, object_id: str) -> bytes:
        self.calls.append(("read", object_id))
        self._maybe_fail("read", object_id)
        if object_id not in self._store:
            raise ObjectNotFoundError(object_id)
        return self._store[object_id]

    async def write(self, object_id: str, data: bytes) -> None:
        self.calls.append(("write", object_id))
        self._maybe_fail("write", object_id)
        if object_id not in self._store:
            raise ObjectNotFoundError(object_id)
        current = self._store[object_id]
        if len(data) > len(current):
            raise InvalidRequestError(
                f"write of {len(data)} bytes exceeds object size {len(current)}"
            )
        self._store[object_id] = bytes(data) + current[len(data):]

    async def free(self, object_id: str) -> None:
        self.calls.append(("free", object_id))
        self._maybe_fail("free", object_id)
        self._store.pop(object_id, None)   # unknown / already freed: silent no-op