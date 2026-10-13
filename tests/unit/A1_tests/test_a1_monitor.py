import pytest

from memory_node.monitor import NodeResourceMonitor


def test_resource_state_values_in_range():
    s = NodeResourceMonitor("node-1").get_resource_state("node-1")
    assert s.free_ram > 0
    assert 0.0 <= s.cpu_usage <= 100.0
    assert 0.0 <= s.health <= 1.0


def test_capacity_stats_are_consistent():
    c = NodeResourceMonitor("node-1").get_capacity_stats("node-1")
    assert c.total_ram > 0
    assert c.used_ram + c.free_ram == c.total_ram


def test_wrong_node_id_is_rejected():
    m = NodeResourceMonitor("node-1")
    with pytest.raises(ValueError):
        m.get_resource_state("node-2")
    with pytest.raises(ValueError):
        m.get_capacity_stats("node-2")