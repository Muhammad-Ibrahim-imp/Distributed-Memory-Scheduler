import pytest

from memory_node.metadata.metadata import MetadataStore, ObjectMetadata
from common.types.errors import InvalidRequestError, ObjectNotFoundError


def rec(oid="o1", owner="alice", size=10):
    return ObjectMetadata(object_id=oid, owner=owner, size=size, created_at=1.0)


def test_count_starts_at_zero():
    assert MetadataStore().count() == 0


def test_sha256_defaults_to_none():
    assert rec().sha256 is None


def test_add_then_get():
    m = MetadataStore()
    m.add(rec())
    assert m.count() == 1
    assert m.get("o1").owner == "alice"
    assert m.has("o1") is True


def test_duplicate_add_rejected():
    m = MetadataStore()
    m.add(rec())
    with pytest.raises(InvalidRequestError):
        m.add(rec())


def test_get_missing_raises():
    with pytest.raises(ObjectNotFoundError):
        MetadataStore().get("nope")


def test_remove_returns_record_and_updates_count():
    m = MetadataStore()
    m.add(rec())
    removed = m.remove("o1")
    assert removed.object_id == "o1"
    assert m.count() == 0


def test_remove_missing_raises():
    with pytest.raises(ObjectNotFoundError):
        MetadataStore().remove("nope")


def test_set_sha256():
    m = MetadataStore()
    m.add(rec())
    m.set_sha256("o1", "abc123")
    assert m.get("o1").sha256 == "abc123"