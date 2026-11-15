import pytest

from memory_node.allocator.allocator import Allocator


def test_full_capacity_available_at_start():
    assert Allocator(1000).available() == 1000


def test_reserve_reduces_available():
    a = Allocator(1000)
    assert a.reserve(400) is True
    assert a.used == 400
    assert a.available() == 600


def test_reserve_exact_capacity_succeeds():
    a = Allocator(1000)
    assert a.reserve(1000) is True
    assert a.available() == 0


def test_reserve_over_capacity_fails_and_changes_nothing():
    a = Allocator(1000)
    a.reserve(600)
    assert a.reserve(500) is False
    assert a.used == 600


def test_release_restores_space():
    a = Allocator(1000)
    a.reserve(400)
    a.release(400)
    assert a.available() == 1000


def test_release_more_than_used_raises():
    a = Allocator(1000)
    a.reserve(100)
    with pytest.raises(ValueError):
        a.release(200)
    assert a.used == 100


def test_negative_sizes_rejected():
    a = Allocator(1000)
    with pytest.raises(ValueError):
        a.reserve(-1)
    with pytest.raises(ValueError):
        a.release(-1)


def test_invalid_capacity_rejected():
    with pytest.raises(ValueError):
        Allocator(0)
    with pytest.raises(ValueError):
        Allocator(-5)