import pytest

from memory_node.allocator.allocator import Allocator
from memory_node.monitor import NodeResourceMonitor


def make_monitor(capacity=1000):
    alloc = Allocator(capacity)
    return NodeResourceMonitor("node-1", alloc), alloc


def test_resource_state_free_ram_is_the_pool_not_the_host():
    m, alloc = make_monitor(1000)
    assert m.get_resource_state("node-1").free_ram == 1000
    alloc.reserve(400)
    s = m.get_resource_state("node-1")
    assert s.free_ram == 600
    assert 0.0 <= s.cpu_usage <= 100.0
    assert 0.0 <= s.health <= 1.0


def test_resource_state_tracks_release():
    m, alloc = make_monitor(1000)
    alloc.reserve(400)
    alloc.release(400)
    assert m.get_resource_state("node-1").free_ram == 1000


def test_capacity_stats_are_host_ram():
    m, _ = make_monitor(1000)
    c = m.get_capacity_stats("node-1")
    assert c.total_ram > 0
    assert c.used_ram + c.free_ram == c.total_ram


def test_capacity_stats_do_not_depend_on_the_pool():
    m, alloc = make_monitor(1000)
    before = m.get_capacity_stats("node-1").total_ram
    alloc.reserve(500)
    assert m.get_capacity_stats("node-1").total_ram == before


def test_node_report_describes_the_pool():
    m, alloc = make_monitor(1000)
    alloc.reserve(250)
    r = m.get_node_report(object_count=3)
    assert r.node_id == "node-1"
    assert r.total_memory == 1000
    assert r.used_memory == 250
    assert r.available_memory == 750
    assert r.object_count == 3
    assert 0.0 <= r.cpu_percent <= 100.0
    assert 0.0 <= r.health <= 1.0


def test_wrong_node_id_is_rejected():
    m, _ = make_monitor()
    with pytest.raises(ValueError):
        m.get_resource_state("node-2")
    with pytest.raises(ValueError):
        m.get_capacity_stats("node-2")