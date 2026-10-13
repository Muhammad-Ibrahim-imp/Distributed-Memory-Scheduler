import asyncio

from memory_node.node import MemoryNode


def make_node():
    return MemoryNode("node-1", 10_000)


def test_node_constructs():
    node = make_node()
    assert node.node_id == "node-1"
    assert node.allocator.available() == 10_000
    assert node.metadata.count() == 0


def test_node_get_resource_state():
    s = asyncio.run(make_node().get_resource_state("node-1"))
    assert s.free_ram > 0
    assert 0.0 <= s.health <= 1.0


def test_node_get_capacity_stats():
    c = asyncio.run(make_node().get_capacity_stats("node-1"))
    assert c.total_ram > 0
    assert c.used_ram + c.free_ram == c.total_ram


def test_node_get_stats():
    # Depends on NodeStats' real field names (unverified until you pull main).
    stats = asyncio.run(make_node().get_stats())
    assert stats.node_id == "node-1"
    assert stats.object_count == 0
    assert 0.0 <= stats.health <= 1.0