import asyncio
import functools

import pytest

from memory_node.node import MemoryNode
from common.types.context import RequestContext
from common.types.errors import (
    InvalidRequestError, IntegrityError, NotOwnerError, ObjectNotFoundError,
    ObjectUnavailableError, OutOfCapacityError, QuotaExceededError,
)
#from common.types.security import EventType, Result, Severity
from common.types.security import EventType, Result, Severity

HAS_INTEGRITY_TYPE = hasattr(EventType, "OBJECT_INTEGRITY_FAILED")

def run_async(fn):
    """Runs an async test in its own event loop, so no plugin or setting is needed."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def ctx(request_id="req-1"):
    return RequestContext("coordinator", request_id)


def make_node(capacity=10_000, **kwargs):
    node = MemoryNode("node-1", capacity, **kwargs)
    events = []
    node.audit._send = events.append       # capture events instead of logging
    return node, events


# ---------- the full sequence ----------
@run_async
async def test_full_sequence_alloc_write_read_free():
    node, events = make_node()
    await node.alloc(ctx(), "o1", "alice", 100)
    assert node.allocator.used == 100
    assert node.metadata.count() == 1
    await node.write(ctx(), "o1", "alice", b"hello")
    assert await node.read(ctx(), "o1", "alice") == b"hello"
    await node.free(ctx(), "o1", "alice")
    assert node.allocator.used == 0
    assert node.metadata.count() == 0
    assert not node.storage.has("o1")
    assert events == []


# ---------- alloc ----------
@run_async
async def test_alloc_tracks_capacity_metadata_and_quota():
    node, events = make_node()
    await node.alloc(ctx(), "o1", "alice", 100)
    assert node.allocator.used == 100
    meta = node.metadata.get("o1")
    assert meta.owner == "alice" and meta.size == 100 and meta.sha256 is None
    assert node.quotas.usage("alice") == {"bytes": 100, "objects": 1}
    assert not node.storage.has("o1")
    assert events == []


@run_async
async def test_repeated_alloc_is_idempotent():
    node, events = make_node()
    await node.alloc(ctx(), "o1", "alice", 100)
    await node.write(ctx(), "o1", "alice", b"hello")
    await node.alloc(ctx("req-2"), "o1", "alice", 100)      # a retry after a timeout
    assert node.allocator.used == 100
    assert node.metadata.count() == 1
    assert node.quotas.usage("alice") == {"bytes": 100, "objects": 1}
    assert await node.read(ctx(), "o1", "alice") == b"hello"   # data untouched
    assert events == []


@run_async
async def test_alloc_same_id_different_owner_or_size_rejected():
    node, events = make_node()
    await node.alloc(ctx(), "o1", "alice", 100)
    with pytest.raises(InvalidRequestError):
        await node.alloc(ctx(), "o1", "bob", 100)
    with pytest.raises(InvalidRequestError):
        await node.alloc(ctx(), "o1", "alice", 200)
    assert node.allocator.used == 100
    assert node.quotas.usage("alice") == {"bytes": 100, "objects": 1}
    assert node.quotas.usage("bob") == {"bytes": 0, "objects": 0}
    assert events == []


@run_async
async def test_alloc_conflict_message_does_not_reveal_the_existing_owner():
    node, _ = make_node()
    await node.alloc(ctx(), "o1", "alice-secret-id", 100)
    with pytest.raises(InvalidRequestError) as exc:
        await node.alloc(ctx(), "o1", "bob", 100)
    assert "alice-secret-id" not in str(exc.value)
@run_async
async def test_alloc_rejects_invalid_arguments():
    node, _ = make_node()
    for size in (0, -5, "10", True, 1.5, None):
        with pytest.raises(InvalidRequestError):
            await node.alloc(ctx(), "o1", "alice", size)
    with pytest.raises(InvalidRequestError):
        await node.alloc(ctx(), "", "alice", 10)
    with pytest.raises(InvalidRequestError):
        await node.alloc(ctx(), "o1", "", 10)
    assert node.metadata.count() == 0
    assert node.allocator.used == 0


@run_async
async def test_alloc_over_capacity_raises_and_changes_nothing():
    node, events = make_node(1000)
    await node.alloc(ctx(), "a1", "alice", 500)
    await node.alloc(ctx(), "b1", "bob", 500)
    with pytest.raises(OutOfCapacityError):
        await node.alloc(ctx(), "c1", "carol", 1)
    assert node.metadata.count() == 2
    assert node.allocator.used == 1000
    assert node.quotas.usage("carol") == {"bytes": 0, "objects": 0}
    assert "c1" not in node._object_locks
    assert events == []


@run_async
async def test_alloc_over_memory_quota_raises_and_reports():
    node, events = make_node(1000)           # quota is 50% = 500 bytes per client
    await node.alloc(ctx(), "o1", "alice", 300)
    with pytest.raises(QuotaExceededError):
        await node.alloc(ctx("req-7"), "o2", "alice", 300)
    assert node.allocator.used == 300
    assert node.metadata.count() == 1
    e = events[0]
    assert e.event_type == EventType.AUTHZ_DENIED
    assert e.severity == Severity.WARNING
    assert e.actor_id == "alice"
    assert e.request_id == "req-7"
    assert e.extra == {"reason": "quota", "object_id": "o2"}
    await node.alloc(ctx(), "o3", "bob", 300)    # another client is unaffected
    assert node.allocator.used == 600


@run_async
async def test_alloc_over_object_quota_raises():
    node, events = make_node(1000, max_objects_per_client=2)
    await node.alloc(ctx(), "o1", "alice", 1)
    await node.alloc(ctx(), "o2", "alice", 1)
    with pytest.raises(QuotaExceededError):
        await node.alloc(ctx(), "o3", "alice", 1)
    assert node.metadata.count() == 2
    assert events[0].extra["reason"] == "quota"


@run_async
async def test_quota_is_checked_before_capacity():
    node, events = make_node(100)            # quota 50, capacity 100
    with pytest.raises(QuotaExceededError):
        await node.alloc(ctx(), "o1", "alice", 200)    # fails both, quota first
    assert events[0].extra["reason"] == "quota"
    assert node.allocator.used == 0


@run_async
async def test_free_restores_quota_room():
    node, _ = make_node(1000)
    await node.alloc(ctx(), "o1", "alice", 500)
    with pytest.raises(QuotaExceededError):
        await node.alloc(ctx(), "o2", "alice", 1)
    await node.free(ctx(), "o1", "alice")
    await node.alloc(ctx(), "o2", "alice", 500)
    assert node.allocator.used == 500


# ---------- write ----------
@run_async
async def test_write_unknown_object_raises_and_does_not_create():
    node, _ = make_node()
    with pytest.raises(ObjectNotFoundError):
        await node.write(ctx(), "nope", "alice", b"x")
    assert node.metadata.count() == 0
    assert not node.storage.has("nope")


@run_async
async def test_write_wrong_owner_rejected_and_data_unchanged():
    node, events = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.write(ctx(), "o1", "alice", b"hello")
    with pytest.raises(NotOwnerError):
        await node.write(ctx("req-3"), "o1", "bob", b"evil")
    assert await node.read(ctx(), "o1", "alice") == b"hello"
    e = events[0]
    assert e.event_type == EventType.AUTHZ_DENIED
    assert e.actor_id == "bob"
    assert e.request_id == "req-3"
    assert e.extra == {"reason": "not_owner", "object_id": "o1"}


@run_async
async def test_write_larger_than_allocation_rejected_smaller_allowed():
    node, _ = make_node()
    await node.alloc(ctx(), "o1", "alice", 5)
    with pytest.raises(InvalidRequestError):
        await node.write(ctx(), "o1", "alice", b"toolong")
    await node.write(ctx(), "o1", "alice", b"abc")
    assert await node.read(ctx(), "o1", "alice") == b"abc"


@run_async
async def test_write_replaces_whole_object_and_wipes_old_buffer():
    node, events = make_node()
    await node.alloc(ctx(), "o1", "alice", 20)
    await node.write(ctx(), "o1", "alice", b"first")
    old = node.storage._data["o1"]
    await node.write(ctx(), "o1", "alice", b"second")
    assert await node.read(ctx(), "o1", "alice") == b"second"
    assert old.count(0) == len(old)
    assert events == []


@run_async
async def test_write_rejects_non_bytes_and_accepts_bytearray():
    node, _ = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    for bad in ("text", None, 123, [1, 2]):
        with pytest.raises(InvalidRequestError):
            await node.write(ctx(), "o1", "alice", bad)
    await node.write(ctx(), "o1", "alice", bytearray(b"ab"))
    assert await node.read(ctx(), "o1", "alice") == b"ab"


# ---------- read ----------
@run_async
async def test_read_unknown_object_raises():
    node, _ = make_node()
    with pytest.raises(ObjectNotFoundError):
        await node.read(ctx(), "nope", "alice")


@run_async
async def test_read_wrong_owner_rejected_and_reported():
    node, events = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.write(ctx(), "o1", "alice", b"secret")
    with pytest.raises(NotOwnerError):
        await node.read(ctx(), "o1", "bob")
    assert events[0].extra == {"reason": "not_owner", "object_id": "o1"}


@run_async
async def test_read_before_write_returns_empty_bytes():
    node, _ = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    assert await node.read(ctx(), "o1", "alice") == b""


@run_async
async def test_read_before_write_still_checks_owner():
    node, _ = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    with pytest.raises(NotOwnerError):
        await node.read(ctx(), "o1", "bob")
@run_async
async def test_integrity_failure_on_read():
    node, events = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.write(ctx(), "o1", "alice", b"hello")
    node.storage._data["o1"][0] ^= 0xFF          # corrupt one byte in memory
    with pytest.raises(IntegrityError):
        await node.read(ctx("req-9"), "o1", "alice")
    # TAMPER_DETECTED is reserved for B1's token check (M5) and must never be used here
    assert all(e.event_type != EventType.TAMPER_DETECTED for e in events)
    if HAS_INTEGRITY_TYPE:
        e = events[0]
        assert e.event_type == EventType.OBJECT_INTEGRITY_FAILED
        assert e.severity == Severity.CRITICAL
        assert e.result == Result.FAILURE
        assert e.actor_id == "node-1"
        assert e.request_id == "req-9"
        assert e.extra == {"object_id": "o1"}
    else:
        assert events == []

# ---------- unavailable ----------
@run_async
async def test_unavailable_when_locked():
    node, _ = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.write(ctx(), "o1", "alice", b"hello")
    lock = node._lock_for("o1")
    await lock.acquire()
    for call in (
        lambda: node.read(ctx(), "o1", "alice"),
        lambda: node.write(ctx(), "o1", "alice", b"x"),
        lambda: node.free(ctx(), "o1", "alice"),
    ):
        with pytest.raises(ObjectUnavailableError):
            await call()
    lock.release()
    assert await node.read(ctx(), "o1", "alice") == b"hello"


@run_async
async def test_unavailable_when_marked_degraded():
    node, _ = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.write(ctx(), "o1", "alice", b"hello")
    node._unavailable.add("o1")
    with pytest.raises(ObjectUnavailableError):
        await node.read(ctx(), "o1", "alice")
    node._unavailable.discard("o1")
    assert await node.read(ctx(), "o1", "alice") == b"hello"


@run_async
async def test_owner_check_comes_before_availability():
    node, _ = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.write(ctx(), "o1", "alice", b"hello")
    await node._lock_for("o1").acquire()
    with pytest.raises(NotOwnerError):           # bob must not learn it is busy
        await node.read(ctx(), "o1", "bob")


# ---------- free ----------
@run_async
async def test_free_unknown_is_silent_noop():
    node, events = make_node()
    assert await node.free(ctx(), "nope", "alice") is None
    assert node.allocator.used == 0
    assert events == []


@run_async
async def test_free_wrong_owner_rejected_object_survives():
    node, events = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.write(ctx(), "o1", "alice", b"hello")
    with pytest.raises(NotOwnerError):
        await node.free(ctx(), "o1", "bob")
    assert await node.read(ctx(), "o1", "alice") == b"hello"
    assert node.allocator.used == 10
    assert events[0].extra["reason"] == "not_owner"


@run_async
async def test_free_wipes_buffer_and_cleans_up():
    node, _ = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.write(ctx(), "o1", "alice", b"secret")
    buf = node.storage._data["o1"]
    await node.free(ctx(), "o1", "alice")
    assert len(buf) == 6 and buf.count(0) == 6
    assert "o1" not in node._object_locks
    assert node.quotas.usage("alice") == {"bytes": 0, "objects": 0}
    assert node.allocator.used == 0
    assert node.metadata.count() == 0
    assert not node.storage.has("o1")


@run_async
async def test_free_succeeds_when_wipe_reports_failure():
    node, events = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.write(ctx(), "o1", "alice", b"secret")
    node.secure_deleter.wipe = lambda buf: False
    await node.free(ctx("req-5"), "o1", "alice")
    assert node.metadata.count() == 0
    assert node.allocator.used == 0
    e = events[0]
    assert e.event_type == EventType.SECURE_DELETE_FAILED
    assert e.severity == Severity.WARNING
    assert e.result == Result.FAILURE
    assert e.actor_id == "node-1"
    assert e.request_id == "req-5"
    assert e.extra == {"object_id": "o1", "owner": "alice"}


@run_async
async def test_free_succeeds_when_wipe_raises():
    node, events = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.write(ctx(), "o1", "alice", b"secret")

    def boom(buf):
        raise RuntimeError("wipe failed")

    node.secure_deleter.wipe = boom
    await node.free(ctx(), "o1", "alice")
    assert node.metadata.count() == 0
    assert node.allocator.used == 0
    assert events[0].event_type == EventType.SECURE_DELETE_FAILED


@run_async
async def test_free_is_idempotent():
    node, _ = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.free(ctx(), "o1", "alice")
    await node.free(ctx(), "o1", "alice")
    assert node.allocator.used == 0


@run_async
async def test_free_of_allocated_but_unwritten_object():
    node, _ = make_node()
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.free(ctx(), "o1", "alice")        # no bytes stored, must not fail
    assert node.allocator.used == 0
    assert node.metadata.count() == 0


# ---------- robustness ----------
@run_async
async def test_reporting_failure_never_breaks_a_request():
    node, _ = make_node()

    def down(event):
        raise RuntimeError("delivery down")

    node.audit._send = down
    await node.alloc(ctx(), "o1", "alice", 10)
    with pytest.raises(NotOwnerError):           # still NotOwnerError, not RuntimeError
        await node.read(ctx(), "o1", "bob")


@run_async
async def test_many_concurrent_allocs():
    node, _ = make_node()
    await asyncio.gather(*(
        node.alloc(ctx(f"r{i}"), f"o{i}", "alice", 10) for i in range(50)
    ))
    assert node.metadata.count() == 50
    assert node.allocator.used == 500



@run_async
async def test_report_and_resource_state_follow_operations():
    node, _ = make_node(1000)
    await node.alloc(ctx(), "o1", "alice", 300)
    r = await node.get_node_report()
    assert (r.used_memory, r.available_memory, r.object_count) == (300, 700, 1)
    assert (await node.get_resource_state("node-1")).free_ram == 700
    await node.free(ctx(), "o1", "alice")
    r = await node.get_node_report()
    assert (r.used_memory, r.available_memory, r.object_count) == (0, 1000, 0)

@run_async
async def test_identity_update_is_used_in_reports_and_events():
    node, events = make_node()
    node.update_node_identity("node-2")
    assert (await node.get_node_report()).node_id == "node-2"
    assert (await node.get_resource_state("node-2")).free_ram == 10_000
    with pytest.raises(ValueError):
        await node.get_resource_state("node-1")
    await node.alloc(ctx(), "o1", "alice", 10)
    await node.write(ctx(), "o1", "alice", b"x")
    node.secure_deleter.wipe = lambda buf: False
    await node.free(ctx(), "o1", "alice")
    assert events[0].actor_id == "node-2"