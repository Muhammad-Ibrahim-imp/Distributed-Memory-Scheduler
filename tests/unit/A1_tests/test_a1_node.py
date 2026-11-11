import asyncio

from memory_node.node import MemoryNode


def make_node():
    return MemoryNode("node-1", 10_000)


def test_node_constructs():
    node = make_node()
    assert node.node_id == "node-1"
    assert node.allocator.available() == 10_000
    assert node.metadata.count() == 0


def test_node_quota_limits_come_from_fraction():
    node = make_node()
    assert node.quotas.max_memory_per_client == 5_000
    assert node.quotas.max_objects_per_client == 10_000


def test_node_quota_override_for_experiment():
    node = MemoryNode("node-1", 10_000, max_memory_fraction_per_client=1.0,
                      max_objects_per_client=None)
    assert node.quotas.max_memory_per_client == 10_000
    assert node.quotas.max_objects_per_client is None


def test_node_get_resource_state_reports_the_pool():
    s = asyncio.run(make_node().get_resource_state("node-1"))
    assert s.free_ram == 10_000
    assert 0.0 <= s.health <= 1.0


def test_node_get_capacity_stats():
    c = asyncio.run(make_node().get_capacity_stats("node-1"))
    assert c.total_ram > 0
    assert c.used_ram + c.free_ram == c.total_ram


def test_node_get_node_report():
    r = asyncio.run(make_node().get_node_report())
    assert r.node_id == "node-1"
    assert r.total_memory == 10_000
    assert r.used_memory == 0
    assert r.available_memory == 10_000
    assert r.object_count == 0
    assert 0.0 <= r.health <= 1.0