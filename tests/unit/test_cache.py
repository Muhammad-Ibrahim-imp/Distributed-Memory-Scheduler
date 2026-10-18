"""Unit tests for the bounded, version-aware ObjectCache."""

import pytest

from dsm.cache import ObjectCache


def test_put_and_get_returns_version_and_data() -> None:
    """A stored object can be retrieved as its version/data pair."""
    cache = ObjectCache(max_bytes=16)

    cache.put("one", 3, b"value")

    assert cache.get("one") == (3, b"value")


def test_lru_eviction_removes_least_recent_entry() -> None:
    """A cache hit promotes an entry before the next insertion."""
    cache = ObjectCache(max_bytes=100, max_entries=2)
    cache.put("one", 0, b"1")
    cache.put("two", 0, b"2")
    assert cache.get("one") == (0, b"1")

    cache.put("three", 0, b"3")

    assert cache.get("two") is None
    assert cache.get("one") == (0, b"1")
    assert cache.get("three") == (0, b"3")
    assert cache.stats()["evictions"] == 1


def test_byte_limit_evicts_oldest_entries_until_within_limit() -> None:
    """Byte capacity uses stored payload lengths and LRU eviction."""
    cache = ObjectCache(max_bytes=5)
    cache.put("one", 0, b"123")
    cache.put("two", 0, b"45")

    cache.put("three", 0, b"678")

    assert cache.get("one") is None
    assert cache.get("two") == (0, b"45")
    assert cache.get("three") == (0, b"678")
    assert cache.stats()["evictions"] == 1


def test_oversized_object_is_not_cached() -> None:
    """An object larger than total cache capacity is skipped."""
    cache = ObjectCache(max_bytes=2)

    cache.put("large", 0, b"large")

    assert cache.get("large") is None


def test_lower_version_does_not_replace_newer_entry() -> None:
    """Delayed stale readers cannot overwrite a newer cached version."""
    cache = ObjectCache()
    cache.put("one", 4, b"new")

    cache.put("one", 3, b"old")

    assert cache.get("one") == (4, b"new")


def test_invalidate_and_invalidate_many_remove_entries() -> None:
    """Single and batched invalidation remove cached objects."""
    cache = ObjectCache()
    cache.put("one", 0, b"1")
    cache.put("two", 0, b"2")
    cache.put("three", 0, b"3")

    cache.invalidate("one")
    cache.invalidate_many(["two", "missing"])

    assert cache.get("one") is None
    assert cache.get("two") is None
    assert cache.get("three") == (0, b"3")
    cache.clear()
    assert cache.get("three") is None


def test_stats_track_hits_misses_evictions() -> None:
    """Cache statistics include lookup counters and eviction count."""
    cache = ObjectCache(max_bytes=1)
    cache.put("one", 0, b"1")

    assert cache.get("one") == (0, b"1")
    assert cache.get("missing") is None
    cache.put("two", 0, b"2")

    assert cache.stats() == {
        "hits": 1,
        "misses": 1,
        "evictions": 1,
    }


@pytest.mark.parametrize(
    ("max_bytes", "max_entries"),
    [(-1, None), (True, None), (1, -1), (1, True)],
)
def test_invalid_cache_limits_are_rejected(
    max_bytes: int,
    max_entries: int | None,
) -> None:
    """Capacity arguments must be non-negative integer limits."""
    with pytest.raises(ValueError):
        ObjectCache(max_bytes=max_bytes, max_entries=max_entries)
