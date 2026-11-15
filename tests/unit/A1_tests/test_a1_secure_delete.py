import pytest

from memory_node.security.secure_delete import SecureDeleter


def test_wipe_zeroes_buffer_in_place():
    buf = bytearray(b"secret data")
    assert SecureDeleter().wipe(buf) is True
    assert buf == bytearray(11)
    assert len(buf) == 11


def test_wipe_empty_buffer():
    assert SecureDeleter().wipe(bytearray()) is True


def test_wipe_rejects_immutable_bytes():
    with pytest.raises(TypeError):
        SecureDeleter().wipe(b"secret")


def test_wipe_large_buffer():
    buf = bytearray(b"x") * 1_000_000
    assert SecureDeleter().wipe(buf) is True
    assert buf.count(0) == 1_000_000