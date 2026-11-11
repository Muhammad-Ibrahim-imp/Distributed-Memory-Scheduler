from pathlib import Path

import psutil
import pytest

from memory_node.allocator.allocator import Allocator
from memory_node.monitor import NodeResourceMonitor, read_container_memory

NO_CGROUP = Path("/nonexistent-cgroup")


def make_monitor(capacity=1000, cgroup_root=NO_CGROUP):
    alloc = Allocator(capacity)
    return NodeResourceMonitor("node-1", alloc, cgroup_root), alloc


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


# ---------- container awareness (B2) ----------
def test_container_limit_cgroup_v2(tmp_path):
    (tmp_path / "memory.max").write_text("1000000\n")
    (tmp_path / "memory.current").write_text("400000\n")
    assert read_container_memory(tmp_path) == (1_000_000, 400_000)


def test_unlimited_container_cgroup_v2(tmp_path):
    (tmp_path / "memory.max").write_text("max\n")
    (tmp_path / "memory.current").write_text("400000\n")
    assert read_container_memory(tmp_path) is None


def test_container_limit_cgroup_v1(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    (d / "memory.limit_in_bytes").write_text("2000000\n")
    (d / "memory.usage_in_bytes").write_text("500000\n")
    assert read_container_memory(tmp_path) == (2_000_000, 500_000)


def test_unlimited_value_cgroup_v1(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    (d / "memory.limit_in_bytes").write_text("9223372036854771712\n")
    (d / "memory.usage_in_bytes").write_text("500000\n")
    assert read_container_memory(tmp_path) is None


def test_no_cgroup_files_gives_none(tmp_path):
    assert read_container_memory(tmp_path) is None


def test_health_reflects_pressure_inside_a_container(tmp_path):
    (tmp_path / "memory.max").write_text("1000000\n")
    (tmp_path / "memory.current").write_text("995000\n")      # 0.5% available
    m, _ = make_monitor(1000, tmp_path)
    assert m.effective_memory() == (1_000_000, 5_000)
    assert m.get_resource_state("node-1").health <= 0.05


def test_container_limit_below_host_ram_is_used(tmp_path):
    (tmp_path / "memory.max").write_text("2000000\n")
    (tmp_path / "memory.current").write_text("500000\n")
    m, _ = make_monitor(1000, tmp_path)
    assert m.effective_memory() == (2_000_000, 1_500_000)


def test_container_limit_above_host_ram_is_ignored(tmp_path):
    host = psutil.virtual_memory().total
    (tmp_path / "memory.max").write_text(f"{host + 1}\n")
    (tmp_path / "memory.current").write_text("0\n")
    m, _ = make_monitor(1000, tmp_path)
    assert m.effective_memory()[0] == host