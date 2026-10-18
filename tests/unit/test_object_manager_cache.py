"""Cache integration tests for the Object Manager."""

import asyncio
import pytest

from common.types.context import RequestContext
from common.types.errors import (
    DSMTimeoutError,
    IntegrityError,
    NotOwnerError,
    ObjectNotFoundError,
    ObjectUnavailableError,
)
from common.types.context import RequestContext
from common.types.security import TrustState
from dsm.cache import ObjectCache
from dsm.directory.directory import Directory
from dsm.object_manager import ObjectManager, ObjectManagerConfig
from tests.mocks.fakes import FakeNodeClient, FakeScheduler, FakeSecurity


def make_manager(
    cache: ObjectCache | None = None,
    node_timeout: float = 5.0,
) -> tuple[ObjectManager, Directory, FakeNodeClient, FakeSecurity]:
    """Construct a manager with observable fakes and an optional cache."""
    directory = Directory()
    node = FakeNodeClient()
    security = FakeSecurity()
    manager = ObjectManager(
        directory=directory,
        node_client=node,
        scheduler=FakeScheduler(node_ids=["node-1"] * 50),
        security=security,
        config=ObjectManagerConfig(),
        node_timeout=node_timeout,
        cache=cache,
    )
    return manager, directory, node, security


def context(identity: str = "alice") -> RequestContext:
    """Return a request context for one manager operation."""
    return RequestContext(identity=identity, request_id="cache-test")


async def new_object(
    manager: ObjectManager,
    initial_data: bytes = b"",
    size: int = 64,
) -> str:
    """Allocate an object and optionally seed it with data."""
    object_id = await manager.alloc(context(), "alice", size)
    if initial_data:
        # Seed backend contents directly so tests can exercise a genuine cache miss.
        manager.node_client.data[object_id] = initial_data
    return object_id


def node_calls(node: FakeNodeClient, method: str) -> int:
    """Count calls of one method in the fake's call log."""
    return sum(call_name == method for call_name, _ in node.call_log)


async def test_first_read_misses_and_second_read_hits() -> None:
    """A successful first fetch populates the cache for the next read."""
    cache = ObjectCache()
    manager, _, node, _ = make_manager(cache)
    object_id = await new_object(manager, b"payload")

    assert await manager.read(context(), "alice", object_id) == b"payload"
    assert node_calls(node, "read") == 1
    assert await manager.read(context(), "alice", object_id) == b"payload"
    assert node_calls(node, "read") == 1
    assert cache.stats()["hits"] == 1


async def test_write_through_read_returns_new_data_without_node_read() -> None:
    """Successful writes populate cache at the incremented version."""
    cache = ObjectCache()
    manager, _, node, _ = make_manager(cache)
    object_id = await new_object(manager, b"old")

    await manager.write(context(), "alice", object_id, b"new")
    reads_before = node_calls(node, "read")

    assert await manager.read(context(), "alice", object_id) == b"new"
    assert node_calls(node, "read") == reads_before


async def test_directory_version_mismatch_refetches() -> None:
    """A cache entry is not served when Directory metadata advances."""
    cache = ObjectCache()
    manager, directory, node, _ = make_manager(cache)
    object_id = await new_object(manager, b"data")
    assert await manager.read(context(), "alice", object_id) == b"data"

    directory.bump_version(object_id)
    assert await manager.read(context(), "alice", object_id) == b"data"

    assert node_calls(node, "read") == 2


async def test_free_invalidates_cache_and_later_read_is_unknown() -> None:
    """Free removes cached bytes along with Directory metadata."""
    cache = ObjectCache()
    manager, _, _, _ = make_manager(cache)
    object_id = await new_object(manager, b"payload")
    await manager.read(context(), "alice", object_id)

    await manager.free(context(), "alice", object_id)

    assert cache.get(object_id) is None
    with pytest.raises(ObjectNotFoundError):
        await manager.read(context(), "alice", object_id)


async def test_revocation_invalidates_cache_and_blocks_reads() -> None:
    """Revocation evicts affected entries and prevents cached reads."""
    cache = ObjectCache()
    manager, _, _, _ = make_manager(cache)
    object_id = await new_object(manager, b"secret")
    await manager.read(context(), "alice", object_id)

    assert manager.handle_node_revoked("node-1", 1.0) == [object_id]

    assert cache.get(object_id) is None
    with pytest.raises(ObjectUnavailableError):
        await manager.read(context(), "alice", object_id)


async def test_quarantine_keeps_cached_reads_available() -> None:
    """Quarantine does not invalidate readable cache entries."""
    cache = ObjectCache()
    manager, _, node, _ = make_manager(cache)
    object_id = await new_object(manager, b"readable")
    assert await manager.read(context(), "alice", object_id) == b"readable"
    read_count = node_calls(node, "read")

    manager.handle_node_trust_state_changed("node-1", TrustState.QUARANTINED, 1.0)

    assert await manager.read(context(), "alice", object_id) == b"readable"
    assert node_calls(node, "read") == read_count


async def test_non_owner_cannot_use_cached_entry() -> None:
    """Authorization is checked before cache lookup."""
    cache = ObjectCache()
    manager, _, node, _ = make_manager(cache)
    object_id = await new_object(manager, b"private")
    assert await manager.read(context(), "alice", object_id) == b"private"
    calls_before = node_calls(node, "read")
    hits_before = cache.stats()["hits"]

    with pytest.raises(NotOwnerError):
        await manager.read(context("bob"), "bob", object_id)

    assert node_calls(node, "read") == calls_before
    assert cache.stats()["hits"] == hits_before


async def test_failed_write_invalidates_cache_and_next_read_refetches() -> None:
    """Any node write error drops cached data before it can be served."""
    cache = ObjectCache()
    manager, _, node, _ = make_manager(cache)
    object_id = await new_object(manager, b"before")
    assert await manager.read(context(), "alice", object_id) == b"before"
    node.fail_next_write.append(IntegrityError("node rejected write"))

    with pytest.raises(IntegrityError):
        await manager.write(context(), "alice", object_id, b"after")

    assert cache.get(object_id) is None
    assert await manager.read(context(), "alice", object_id) == b"before"
    assert node_calls(node, "read") == 2


async def test_timed_out_write_invalidates_cache() -> None:
    """A timeout's unknown outcome also invalidates cached data."""
    cache = ObjectCache()
    manager, _, node, _ = make_manager(cache, node_timeout=0.01)
    object_id = await new_object(manager, b"before")
    assert await manager.read(context(), "alice", object_id) == b"before"
    node.call_delays["write"] = 0.05

    with pytest.raises(DSMTimeoutError):
        await manager.write(context(), "alice", object_id, b"after")

    assert cache.get(object_id) is None


async def test_slow_reader_cannot_overwrite_newer_write_through_entry() -> None:
    """An old captured version cannot replace a newer cached write."""
    cache = ObjectCache()
    manager, _, node, _ = make_manager(cache)
    object_id = await new_object(manager, b"old")
    read_started = asyncio.Event()
    release_read = asyncio.Event()
    original_read = node.read

    async def delayed_read(
        ctx: RequestContext,
        node_id: str,
        requested_id: str,
        owner_identity: str,
    ) -> bytes:
        """Capture the old node value, then delay returning it to the manager."""
        data = await original_read(ctx, node_id, requested_id, owner_identity)
        read_started.set()
        await release_read.wait()
        return data

    node.read = delayed_read  # type: ignore[method-assign]
    reader = asyncio.create_task(manager.read(context(), "alice", object_id))
    await read_started.wait()
    await manager.write(context(), "alice", object_id, b"new")
    release_read.set()

    assert await reader == b"old"
    assert await manager.read(context(), "alice", object_id) == b"new"


async def test_concurrent_reads_and_writes_only_observe_complete_values() -> None:
    """Concurrent operations expose only full old/new values and finish current."""
    cache = ObjectCache()
    manager, _, node, _ = make_manager(cache)
    object_id = await new_object(manager, b"old")
    node.call_delays["read"] = 0.001
    node.call_delays["write"] = 0.001
    valid_values = {b"old", b"new-1", b"new-2", b"newest"}

    async def reader() -> bytes:
        """Read once and verify the value belongs to the complete write set."""
        value = await manager.read(context(), "alice", object_id)
        assert value in valid_values
        return value

    async def writer(value: bytes) -> int:
        """Replace the complete object with a known value."""
        return await manager.write(context(), "alice", object_id, value)

    await asyncio.gather(
        reader(),
        writer(b"new-1"),
        reader(),
        writer(b"new-2"),
        reader(),
    )
    await writer(b"newest")

    assert await manager.read(context(), "alice", object_id) == b"newest"


async def test_cache_disabled_preserves_node_read_behavior() -> None:
    """Without an injected cache every read continues to call the node."""
    manager, _, node, _ = make_manager()
    object_id = await new_object(manager, b"data")

    assert await manager.read(context(), "alice", object_id) == b"data"
    assert await manager.read(context(), "alice", object_id) == b"data"
    assert node_calls(node, "read") == 2
