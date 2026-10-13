from memory_node.allocator.allocator import Allocator


def test_full_capacity_available_at_start():
    assert Allocator(1000).available() == 1000


def test_used_starts_at_zero():
    assert Allocator(1000).used == 0