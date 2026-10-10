from dsm.directory.directory import Directory, ObjectRecord, ObjectState
from tests.mocks.fakes import FakeMemoryNode, FakeScheduler


async def test_fake_node_roundtrip():
    # store then load should give back the same bytes
    node = FakeMemoryNode()
    await node.store("obj-1", "alice", b"hello")
    assert await node.load("obj-1", "alice") == b"hello"


def test_directory_put_get():
    # directory returns what we put in, and None for unknown ids
    d = Directory()
    d.put(ObjectRecord(object_id="obj-1", owner="alice", size=5, node_id="node-1"))
    assert d.get("obj-1").state == ObjectState.ALLOCATED
    assert d.get("nope") is None


def test_fake_scheduler_returns_decision():
    # the scheduler mock must return a node_id for any object
    decision = FakeScheduler().select_node_from_state(object_id="obj-1", object_size=5)
    assert decision.node_id == "node-1"