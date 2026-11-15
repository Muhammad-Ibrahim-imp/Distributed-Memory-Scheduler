"""Per-object versions and write locks for the DSM runtime."""

import asyncio

from common.types.errors import ObjectNotFoundError


class ConsistencyManager:
    """Track object versions and serialize mutations to each object."""

    def __init__(self) -> None:
        self._versions: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def register(self, object_id: str, version: int = 0) -> None:
        """Start tracking an allocated object at its initial version."""
        self._versions[object_id] = version
        if object_id not in self._locks:
            self._locks[object_id] = asyncio.Lock()

    def get_version(self, object_id: str) -> int | None:
        """Return the current version, or None when the object is untracked."""
        return self._versions.get(object_id)

    def get_lock(self, object_id: str) -> asyncio.Lock | None:
        """Return the object's write lock, or None when it is untracked."""
        return self._locks.get(object_id)

    def bump_version(self, object_id: str) -> int:
        """Advance a tracked object after a successful write."""
        version = self._versions.get(object_id)
        if version is None:
            raise ObjectNotFoundError(object_id)
        version += 1
        self._versions[object_id] = version
        return version

    def remove(self, object_id: str) -> None:
        """Stop tracking a freed object."""
        self._versions.pop(object_id, None)
        self._locks.pop(object_id, None)
