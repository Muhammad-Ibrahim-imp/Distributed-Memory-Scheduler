"""Read and write behavior for the DSM Object Manager."""

import asyncio

import pytest

from common.types.context import RequestContext
from common.types.errors import (
    DSMTimeoutError,
    IntegrityError,
    InvalidRequestError,
    NotOwnerError,
    ObjectNotFoundError,
    ObjectUnavailableError,
)
from dsm.directory.directory import Directory, ObjectState
from dsm.object_manager import ObjectManager, ObjectManagerConfig
from tests.mocks.fakes import FakeNodeClient, FakeScheduler, FakeSecurity


def make_manager(
    node_ids: list[str] | None = None,
    node_timeout: float = 5.0,
) -> tuple[ObjectManager, Directory, FakeNodeClient]:
    """Create an Object Manager with test doubles."""
    directory = Directory()
    node_client = FakeNodeClient()
    scheduler = FakeScheduler(
        node_ids=node_ids if node_ids is not None else ["node-1"] * 25
    )
    manager = ObjectManager(
        directory=directory,
        node_client=node_client,
        scheduler=scheduler,
        security=FakeSecurity(),
        config=ObjectManagerConfig(),
        node_timeout=node_timeout,
    )
    return manager, directory, node_client


def make_context(identity: str = "alice") -> RequestContext:
    """Create a request context for one test operation."""
    return RequestContext(identity=identity, request_id="read-write-test")


async def allocate(manager: ObjectManager, size: int = 32) -> str:
    """Allocate one object for a read/write test."""
    return await manager.alloc(make_context(), "alice", size)


async def test_read_after_write_returns_data_and_unknown_read_raises() -> None:
    """Reads return the last completed full replacement."""
    manager, _, _ = make_manager()
    object_id = await allocate(manager)

    assert await manager.write(make_context(), "alice", object_id, b"hello") == 1
    assert await manager.read(make_context(), "alice", object_id) == b"hello"

    with pytest.raises(ObjectNotFoundError):
        await manager.read(make_context(), "alice", "unknown")


async def test_read_by_non_owner_does_not_call_node() -> None:
    """Authorization denial happens before the node is contacted."""
    manager, _, node = make_manager()
    object_id = await allocate(manager)
    call_count = len(node.call_log)

    with pytest.raises(NotOwnerError):
        await manager.read(make_context("mallory"), "mallory", object_id)

    assert len(node.call_log) == call_count


async def test_read_quarantined_is_allowed_and_revoked_is_blocked() -> None:
    """Quarantine permits reads, while revocation blocks reads before node I/O."""
    manager, directory, node = make_manager()
    quarantined_id = await allocate(manager)
    revoked_id = await allocate(manager)
    directory.get(quarantined_id).state = ObjectState.QUARANTINED
    directory.get(revoked_id).state = ObjectState.REVOKED
    call_count = len(node.call_log)

    assert await manager.read(make_context(), "alice", quarantined_id) == b""
    call_count += 1
    with pytest.raises(ObjectUnavailableError):
        await manager.read(make_context(), "alice", revoked_id)

    assert len(node.call_log) == call_count


async def test_write_bumps_version_and_unknown_write_does_not_create() -> None:
    """Each successful write increments metadata; unknown ids stay unknown."""
    manager, directory, _ = make_manager()
    object_id = await allocate(manager)

    assert await manager.write(make_context(), "alice", object_id, b"one") == 1
    assert await manager.write(make_context(), "alice", object_id, b"two") == 2
    assert directory.get(object_id).version == 2

    with pytest.raises(ObjectNotFoundError):
        await manager.write(make_context(), "alice", "unknown", b"no")
    assert directory.get("unknown") is None


@pytest.mark.parametrize("state", [ObjectState.QUARANTINED, ObjectState.REVOKED])
async def test_write_on_unavailable_state_does_not_change_version(
    state: ObjectState,
) -> None:
    """Writes are rejected on quarantined and revoked objects."""
    manager, directory, node = make_manager()
    object_id = await allocate(manager)
    directory.get(object_id).state = state
    call_count = len(node.call_log)

    with pytest.raises(ObjectUnavailableError):
        await manager.write(make_context(), "alice", object_id, b"blocked")

    assert directory.get(object_id).version == 0
    assert len(node.call_log) == call_count


async def test_write_rejects_oversized_data_and_non_bytes() -> None:
    """Writes accept bytes-like values only when they fit the allocation."""
    manager, directory, _ = make_manager()
    object_id = await allocate(manager, size=3)

    with pytest.raises(InvalidRequestError):
        await manager.write(make_context(), "alice", object_id, b"four")
    with pytest.raises(InvalidRequestError):
        await manager.write(make_context(), "alice", object_id, "str")  # type: ignore[arg-type]

    assert await manager.write(make_context(), "alice", object_id, bytearray()) == 1
    assert directory.get(object_id).version == 1


async def test_node_write_error_preserves_version_and_integrity_error() -> None:
    """Failed writes do not bump the version and node errors propagate."""
    manager, directory, node = make_manager()
    object_id = await allocate(manager)
    expected_error = IntegrityError("digest mismatch")
    node.fail_next_write.append(expected_error)

    with pytest.raises(IntegrityError) as raised:
        await manager.write(make_context(), "alice", object_id, b"changed")

    assert raised.value is expected_error
    assert directory.get(object_id).version == 0


async def test_slow_node_write_is_converted_to_dsm_timeout() -> None:
    """A node write exceeding the configured deadline raises DSMTimeoutError."""
    manager, directory, node = make_manager(node_timeout=0.01)
    object_id = await allocate(manager)
    node.call_delays["write"] = 0.05

    with pytest.raises(DSMTimeoutError) as raised:
        await manager.write(make_context(), "alice", object_id, b"slow")

    assert isinstance(raised.value.__cause__, TimeoutError)
    assert directory.get(object_id).version == 0
    assert node.active_write_calls == 0


async def test_twenty_concurrent_writes_to_one_object_are_serialized() -> None:
    """A single object's writes execute one at a time and each bump once."""
    manager, directory, node = make_manager()
    object_id = await allocate(manager)
    node.call_delays["write"] = 0.001

    versions = await asyncio.gather(
        *(
            manager.write(make_context(), "alice", object_id, f"{index}".encode())
            for index in range(20)
        )
    )

    assert sorted(versions) == list(range(1, 21))
    assert directory.get(object_id).version == 20
    assert node.max_concurrent_write_calls_by_object[object_id] == 1
    assert node.max_concurrent_write_calls == 1


async def test_writes_to_different_objects_overlap() -> None:
    """Independent object locks allow concurrent node writes."""
    manager, _, node = make_manager(["node-1", "node-1"])
    first_id = await allocate(manager)
    second_id = await allocate(manager)
    node.call_delays["write"] = 0.02

    await asyncio.gather(
        manager.write(make_context(), "alice", first_id, b"first"),
        manager.write(make_context(), "alice", second_id, b"second"),
    )

    assert node.max_concurrent_write_calls > 1


async def test_waiting_writer_rechecks_revoked_state_after_lock() -> None:
    """A queued writer observes a revocation made while it awaited the lock."""
    manager, directory, node = make_manager()
    object_id = await allocate(manager)
    lock = directory.get_lock(object_id)
    assert lock is not None
    await lock.acquire()
    waiting_writer = asyncio.create_task(
        manager.write(make_context(), "alice", object_id, b"queued")
    )
    await asyncio.sleep(0)

    directory.mark_node_state("node-1", ObjectState.REVOKED)
    lock.release()
    with pytest.raises(ObjectUnavailableError):
        await waiting_writer

    assert directory.get(object_id).version == 0
    assert len([call for call in node.call_log if call[0] == "write"]) == 0


async def test_slow_read_alloc_and_free_use_node_timeout() -> None:
    """Every node operation, including the prior alloc/free paths, is bounded."""
    manager, directory, node = make_manager(node_timeout=0.01)
    node.call_delays["alloc"] = 0.05

    with pytest.raises(DSMTimeoutError):
        await manager.alloc(make_context(), "alice", 8)
    assert directory.get(next(
        call[1][2] for call in node.call_log if call[0] == "alloc"
    )) is None

    node.call_delays["alloc"] = 0
    object_id = await allocate(manager)
    node.call_delays["read"] = 0.05
    with pytest.raises(DSMTimeoutError):
        await manager.read(make_context(), "alice", object_id)

    node.call_delays["read"] = 0
    node.call_delays["free"] = 0.05
    with pytest.raises(DSMTimeoutError):
        await manager.free(make_context(), "alice", object_id)
    assert directory.get(object_id) is not None
