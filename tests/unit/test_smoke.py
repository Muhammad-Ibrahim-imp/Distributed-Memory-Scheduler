from dsm.directory.directory import Directory, ObjectRecord, ObjectState
from common.types.context import RequestContext
from tests.mocks.fakes import FakeNodeClient


async def test_fake_node_roundtrip():
    node = FakeNodeClient()
    ctx = RequestContext(identity="alice", request_id="req-1")
    await node.alloc(ctx, "node-1", "obj-1", "alice", 5)
    await node.write(ctx, "node-1", "obj-1", "alice", b"hello")
    assert await node.read(ctx, "node-1", "obj-1", "alice") == b"hello"


def test_directory_put_get():
    # directory returns what we put in, and None for unknown ids
    d = Directory()
    d.put(ObjectRecord(object_id="obj-1", owner="alice", size=5, node_id="node-1"))
    assert d.get("obj-1").state == ObjectState.ALLOCATED
    assert d.get("nope") is None