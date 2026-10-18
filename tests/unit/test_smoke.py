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
    d.put(ObjectRecord(
        object_id="obj-1",
        owner_identity="alice",
        size=5,
        node_id="node-1",
    ))
    record = d.get("obj-1")
    assert record is not None
    assert record.owner_identity == "alice"
    assert record.node_id == "node-1"
    assert record.size == 5
    assert record.version == 0
    assert record.state == ObjectState.ALLOCATED
    assert d.get("nope") is None