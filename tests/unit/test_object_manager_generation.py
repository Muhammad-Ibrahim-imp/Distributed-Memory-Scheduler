"""Coordinator-generation lifecycle coverage for the DSM object manager."""

from uuid import UUID

import pytest

from common.types.context import RequestContext
from common.types.errors import ObjectNotFoundError, ObjectUnavailableError
from dsm.cache import ObjectCache
from dsm.directory.directory import Directory
from dsm.object_manager import ObjectManager, ObjectManagerConfig
from dsm.runtime import Runtime
from tests.mocks.fakes import FakeNodeClient, FakeScheduler, FakeSecurity


def make_manager(
    generation: str = "generation-1",
    cache: ObjectCache | None = None,
) -> tuple[ObjectManager, Directory, FakeNodeClient]:
    directory = Directory(coordinator_generation=generation)
    node = FakeNodeClient()
    manager = ObjectManager(
        directory=directory,
        node_client=node,
        scheduler=FakeScheduler(node_ids=["node-1"] * 8),
        security=FakeSecurity(),
        config=ObjectManagerConfig(),
        cache=cache,
        coordinator_generation=generation,
    )
    return manager, directory, node


def context(generation: str = "generation-1") -> RequestContext:
    return RequestContext(
        identity="alice",
        request_id="generation-test",
        coordinator_generation=generation,
    )


async def test_generation_change_invalidates_directory_versions_and_cache() -> None:
    cache = ObjectCache()
    manager, directory, _ = make_manager(cache=cache)
    object_id = await manager.alloc(context(), "alice", 32)
    await manager.write(context(), "alice", object_id, b"old-generation")
    assert await manager.read(context(), "alice", object_id) == b"old-generation"

    assert manager.start_generation("generation-2") == [object_id]

    assert directory.coordinator_generation == "generation-2"
    assert directory.get(object_id) is None
    assert directory.get_lock(object_id) is None
    assert not directory.is_current(object_id, 1)
    assert cache.get(object_id) is None
    with pytest.raises(ObjectNotFoundError):
        await manager.read(context("generation-2"), "alice", object_id)
    with pytest.raises(ObjectNotFoundError):
        await manager.write(context("generation-2"), "alice", object_id, b"stale")
    with pytest.raises(ObjectNotFoundError):
        await manager.free(context("generation-2"), "alice", object_id)


async def test_new_runtime_clears_reused_directory_and_cache() -> None:
    cache = ObjectCache()
    manager, directory, node = make_manager(cache=cache)
    old_id = await manager.alloc(context(), "alice", 32)
    await manager.read(context(), "alice", old_id)

    runtime = Runtime(
        directory=directory,
        node_client=node,
        scheduler=FakeScheduler(node_ids=["node-1"]),
        security=FakeSecurity(),
        cache=cache,
        coordinator_generation="generation-2",
    )

    assert directory.coordinator_generation == "generation-2"
    assert directory.get(old_id) is None
    assert directory.get_lock(old_id) is None
    assert cache.get(old_id) is None
    with pytest.raises(ObjectNotFoundError):
        await runtime.bind("alice").read(old_id)


async def test_generation_qualified_ids_prevent_cross_generation_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _, _ = make_manager()
    repeated_nonce = UUID("12345678-1234-4234-8234-123456789abc")
    monkeypatch.setattr("dsm.object_manager.dsm_alloc.uuid4", lambda: repeated_nonce)

    old_id = await manager.alloc(context(), "alice", 32)
    manager.start_generation("generation-2")
    new_id = await manager.alloc(context("generation-2"), "alice", 32)

    assert old_id != new_id
    assert UUID(old_id.rsplit(".", 1)[1]).version == 4
    assert UUID(new_id.rsplit(".", 1)[1]).version == 4
    with pytest.raises(ObjectNotFoundError):
        await manager.read(context("generation-2"), "alice", old_id)


async def test_node_requests_carry_active_generation() -> None:
    directory = Directory(coordinator_generation="generation-1")
    node = FakeNodeClient()
    runtime = Runtime(
        directory=directory,
        node_client=node,
        scheduler=FakeScheduler(node_ids=["node-1"]),
        security=FakeSecurity(),
        coordinator_generation="generation-1",
    )
    await runtime.bind("alice").alloc(32)

    alloc_context = next(args[0] for name, args in node.call_log if name == "alloc")
    assert alloc_context.coordinator_generation == "generation-1"


async def test_old_request_context_cannot_allocate_in_new_generation() -> None:
    manager, _, _ = make_manager()
    manager.start_generation("generation-2")

    with pytest.raises(ObjectUnavailableError):
        await manager.alloc(context(), "alice", 32)
