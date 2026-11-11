import pytest

from memory_node.storage.storage import ObjectStore
from common.types.errors import ObjectNotFoundError


def test_store_starts_empty():
    assert ObjectStore()._data == {}


def test_put_then_get_returns_same_bytes():
    s = ObjectStore()
    s.put("o1", b"hello")
    assert s.get("o1") == b"hello"


def test_get_returns_bytes_type():
    s = ObjectStore()
    s.put("o1", b"hello")
    assert isinstance(s.get("o1"), bytes)


def test_put_stores_a_copy():
    s = ObjectStore()
    src = bytearray(b"abc")
    s.put("o1", src)
    src[0] = 0
    assert s.get("o1") == b"abc"


def test_put_replaces_existing():
    s = ObjectStore()
    s.put("o1", b"one")
    s.put("o1", b"two")
    assert s.get("o1") == b"two"


def test_has():
    s = ObjectStore()
    assert s.has("o1") is False
    s.put("o1", b"x")
    assert s.has("o1") is True


def test_get_missing_raises():
    with pytest.raises(ObjectNotFoundError):
        ObjectStore().get("nope")


def test_delete_returns_buffer_and_removes():
    s = ObjectStore()
    s.put("o1", b"secret")
    buf = s.delete("o1")
    assert isinstance(buf, bytearray)
    assert bytes(buf) == b"secret"
    assert s.has("o1") is False


def test_delete_missing_raises():
    with pytest.raises(ObjectNotFoundError):
        ObjectStore().delete("nope")