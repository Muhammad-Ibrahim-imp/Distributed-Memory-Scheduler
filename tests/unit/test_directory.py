# tests/test_directory.py
import asyncio
import pytest

from common.types.errors import ObjectNotFoundError
from dsm.directory.directory import Directory, ObjectRecord, ObjectState


def make_record(object_id="obj1", node_id="nodeA"):
    # keyword arguments because the field order is object_id, owner_identity, size, node_id
    return ObjectRecord(object_id=object_id, owner_identity="alice", size=100, node_id=node_id)


def test_put_then_get():
    d = Directory()
    d.put(make_record())
    record = d.get("obj1")
    assert record.owner_identity == "alice"        # stored what we gave it
    assert record.version == 0                     # new objects start at version 0
    assert record.state == ObjectState.ALLOCATED


def test_get_unknown_returns_none():
    d = Directory()
    assert d.get("missing") is None                # unknown id gives None, Object Manager raises


def test_put_creates_a_lock():
    d = Directory()
    d.put(make_record())
    assert d.get_lock("obj1") is not None          # every stored object has a lock
    assert d.get_lock("missing") is None


def test_remove_is_idempotent():
    d = Directory()
    d.put(make_record())
    d.remove("obj1")
    d.remove("obj1")                               # second remove must not raise
    assert d.get("obj1") is None
    assert d.get_lock("obj1") is None              # the lock goes with the record


def test_bump_version():
    d = Directory()
    d.put(make_record())
    assert d.bump_version("obj1") == 1
    assert d.bump_version("obj1") == 2


def test_bump_version_unknown_raises():
    d = Directory()
    with pytest.raises(ObjectNotFoundError):
        d.bump_version("missing")


def test_mark_node_state_only_hits_that_node():
    d = Directory()
    d.put(make_record("obj1", "nodeA"))
    d.put(make_record("obj2", "nodeB"))
    affected = d.mark_node_state("nodeA", ObjectState.REVOKED)
    assert affected == ["obj1"]                                # only nodeA objects
    assert d.get("obj1").state == ObjectState.REVOKED
    assert d.get("obj2").state == ObjectState.ALLOCATED        # nodeB untouched


def test_two_coroutines_on_the_same_object_are_serialised():
    d = Directory()
    d.put(make_record())
    order = []                                     # records who entered the locked section

    async def writer(name):
        async with d.get_lock("obj1"):             # only one writer inside at a time
            order.append(name + " in")
            await asyncio.sleep(0)                 # yield, the other writer must still wait
            d.bump_version("obj1")
            order.append(name + " out")

    async def run():
        await asyncio.gather(writer("w1"), writer("w2"))

    asyncio.run(run())

    assert d.get("obj1").version == 2                          # both writes counted
    assert order == ["w1 in", "w1 out", "w2 in", "w2 out"]     # no interleaving