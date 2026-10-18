"""Small synchronous LRU cache for versioned DSM object contents."""

from collections import OrderedDict


class ObjectCache:
    """Cache object bytes with version-aware insertion and bounded storage."""

    def __init__(
        self,
        max_bytes: int = 64 * 1024 * 1024,
        max_entries: int | None = None,
    ) -> None:
        """Create an empty cache bounded by bytes and optionally entry count."""
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0:
            raise ValueError("max_bytes must be a non-negative integer")
        if (
            max_entries is not None
            and (
                not isinstance(max_entries, int)
                or isinstance(max_entries, bool)
                or max_entries < 0
            )
        ):
            raise ValueError("max_entries must be a non-negative integer or None")

        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._entries: OrderedDict[str, tuple[int, bytes]] = OrderedDict()
        self._current_bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, object_id: str) -> tuple[int, bytes] | None:
        """Return a cached version/data pair and promote it to most-recent."""
        entry = self._entries.get(object_id)
        if entry is None:
            self._misses += 1
            return None

        self._entries.move_to_end(object_id)
        self._hits += 1
        return entry

    def put(self, object_id: str, version: int, data: bytes) -> None:
        """Insert an entry unless it is oversized or older than the cached one."""
        cached_entry = self._entries.get(object_id)
        if cached_entry is not None and cached_entry[0] > version:
            return

        value = bytes(data)
        if len(value) > self.max_bytes:
            return

        if cached_entry is not None:
            self._current_bytes -= len(cached_entry[1])
            del self._entries[object_id]

        self._entries[object_id] = (version, value)
        self._current_bytes += len(value)
        self._evict_to_limits()

    def _evict_to_limits(self) -> None:
        """Remove least-recent entries until both configured limits are met."""
        while self._entries and (
            self._current_bytes > self.max_bytes
            or (
                self.max_entries is not None
                and len(self._entries) > self.max_entries
            )
        ):
            _, (_, data) = self._entries.popitem(last=False)
            self._current_bytes -= len(data)
            self._evictions += 1

    def invalidate(self, object_id: str) -> None:
        """Remove one object from the cache if it is present."""
        entry = self._entries.pop(object_id, None)
        if entry is not None:
            self._current_bytes -= len(entry[1])

    def invalidate_many(self, object_ids: list[str]) -> None:
        """Remove each listed object from the cache."""
        for object_id in object_ids:
            self.invalidate(object_id)

    def clear(self) -> None:
        """Remove all entries without resetting accumulated statistics."""
        self._entries.clear()
        self._current_bytes = 0

    def stats(self) -> dict[str, int]:
        """Return cache hit, miss, and eviction counters."""
        return {
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
        }


__all__ = ["ObjectCache"]