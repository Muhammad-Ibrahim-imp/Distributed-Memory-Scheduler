import pytest

from memory_node.security.quotas import QuotaTracker
from common.types.errors import QuotaExceededError


def test_no_limits_means_unlimited():
    assert QuotaTracker().check_within_quota("a", 10**12) is None


def test_memory_limit():
    q = QuotaTracker(max_memory_per_client=100)
    q.check_within_quota("a", 100)
    q.record_alloc("a", 60)
    q.check_within_quota("a", 40)
    with pytest.raises(QuotaExceededError):
        q.check_within_quota("a", 41)


def test_object_limit():
    q = QuotaTracker(max_objects_per_client=2)
    q.record_alloc("a", 1)
    q.check_within_quota("a", 1)
    q.record_alloc("a", 1)
    with pytest.raises(QuotaExceededError):
        q.check_within_quota("a", 1)


def test_clients_are_independent():
    q = QuotaTracker(max_memory_per_client=100)
    q.record_alloc("a", 100)
    q.check_within_quota("b", 100)
    with pytest.raises(QuotaExceededError):
        q.check_within_quota("a", 1)


def test_free_restores_room():
    q = QuotaTracker(max_memory_per_client=100)
    q.record_alloc("a", 100)
    with pytest.raises(QuotaExceededError):
        q.check_within_quota("a", 1)
    q.record_free("a", 100)
    q.check_within_quota("a", 100)


def test_free_more_than_recorded_raises():
    q = QuotaTracker()
    q.record_alloc("a", 10)
    with pytest.raises(ValueError):
        q.record_free("a", 20)
    with pytest.raises(ValueError):
        q.record_free("b", 1)


def test_from_fraction():
    q = QuotaTracker.from_fraction(1000, 0.5, 10)
    assert q.max_memory_per_client == 500
    assert q.max_objects_per_client == 10
    q.check_within_quota("a", 500)
    with pytest.raises(QuotaExceededError):
        q.check_within_quota("a", 501)


def test_from_fraction_invalid():
    with pytest.raises(ValueError):
        QuotaTracker.from_fraction(1000, 0)
    with pytest.raises(ValueError):
        QuotaTracker.from_fraction(1000, 1.5)
    with pytest.raises(ValueError):
        QuotaTracker.from_fraction(0, 0.5)


def test_usage_report():
    q = QuotaTracker()
    assert q.usage("a") == {"bytes": 0, "objects": 0}
    q.record_alloc("a", 30)
    assert q.usage("a") == {"bytes": 30, "objects": 1}


def test_negative_bytes_rejected():
    with pytest.raises(ValueError):
        QuotaTracker().check_within_quota("a", -1)