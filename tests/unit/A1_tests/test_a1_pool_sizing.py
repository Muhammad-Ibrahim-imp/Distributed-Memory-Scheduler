import asyncio
import logging

import pytest

from memory_node.monitor import plan_pool_capacity
from memory_node.node import MemoryNode

MIN = 128 * 1024 * 1024


def cgroup(tmp_path, limit):
    (tmp_path / "memory.max").write_text(f"{limit}\n")
    (tmp_path / "memory.current").write_text("0\n")
    return tmp_path


# ---------- the sizing rule ----------
def test_headroom_is_ten_percent_when_that_is_larger():
    p = plan_pool_capacity(10**12, 10_000_000_000)
    assert p.headroom == 1_000_000_000
    assert p.pool == 9_000_000_000
    assert p.clamped is True


def test_headroom_has_a_128_mib_minimum():
    p = plan_pool_capacity(10**12, 1_000_000_000)
    assert p.headroom == MIN
    assert p.pool == 1_000_000_000 - MIN


def test_pool_is_not_clamped_when_configured_capacity_fits():
    p = plan_pool_capacity(500_000_000, 10_000_000_000)
    assert p.pool == 500_000_000
    assert p.clamped is False


def test_startup_refused_when_headroom_exceeds_the_limit():
    with pytest.raises(ValueError):
        plan_pool_capacity(10**9, 100 * 1024 * 1024)


def test_startup_refused_when_nothing_is_left():
    with pytest.raises(ValueError):
        plan_pool_capacity(10**9, MIN)


def test_headroom_is_configurable():
    p = plan_pool_capacity(10**12, 1_000_000_000, 0.5, 0)
    assert p.headroom == 500_000_000
    assert p.pool == 500_000_000


def test_invalid_arguments_are_rejected():
    for args in ((0, 10**9), (10**9, 0), (10**9, 10**9, -0.1),
                 (10**9, 10**9, 1.0), (10**9, 10**9, 0.1, -1)):
        with pytest.raises(ValueError):
            plan_pool_capacity(*args)


# ---------- the node uses it ----------
def test_pool_is_clamped_to_the_container_limit_minus_headroom(tmp_path):
    node = MemoryNode("node-1", 10**12, cgroup_root=cgroup(tmp_path, 1_000_000_000))
    expected = 1_000_000_000 - MIN
    assert node.allocator.total_capacity == expected
    assert node.pool_plan.clamped is True
    r = asyncio.run(node.get_node_report())
    assert r.total_memory == r.available_memory == expected
    assert asyncio.run(node.get_resource_state("node-1")).free_ram == expected


def test_small_configured_capacity_is_left_alone(tmp_path):
    node = MemoryNode("node-1", 10_000, cgroup_root=cgroup(tmp_path, 1_000_000_000))
    assert node.allocator.total_capacity == 10_000
    assert node.pool_plan.clamped is False


def test_node_refuses_to_start_without_usable_capacity(tmp_path):
    with pytest.raises(ValueError):
        MemoryNode("node-1", 10_000,
                   cgroup_root=cgroup(tmp_path, 100 * 1024 * 1024))


def test_node_headroom_is_configurable(tmp_path):
    node = MemoryNode("node-1", 10**12, headroom_fraction=0.5, headroom_min_bytes=0,
                      cgroup_root=cgroup(tmp_path, 1_000_000_000))
    assert node.allocator.total_capacity == 500_000_000


def test_quota_fraction_applies_to_the_clamped_pool(tmp_path):
    node = MemoryNode("node-1", 10**12, cgroup_root=cgroup(tmp_path, 1_000_000_000))
    assert node.quotas.max_memory_per_client == int((1_000_000_000 - MIN) * 0.5)


def test_pool_sizing_is_logged(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="memory_node.node"):
        MemoryNode("node-1", 10**12, cgroup_root=cgroup(tmp_path, 1_000_000_000))
    assert "configured=1000000000000" in caplog.text
    assert "effective_limit=1000000000" in caplog.text
    assert f"headroom={MIN}" in caplog.text
    assert f"pool={1_000_000_000 - MIN}" in caplog.text