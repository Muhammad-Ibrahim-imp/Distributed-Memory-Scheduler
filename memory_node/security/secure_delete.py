class SecureDeleter:
    """Best-effort wipe. Python can't guarantee no other copies exist
    (for example the bytes object B1 passed into write() until it is
    garbage-collected), so this clears OUR buffer, not every copy."""

    def wipe(self, data: bytearray) -> bool:
        """Zeroes the buffer in place. True if every byte is zero afterwards.
        node.free reports SECURE_DELETE_FAILED (WARNING) when this returns False;
        the free still succeeds (M5)."""
        if not isinstance(data, bytearray):
            raise TypeError("only a bytearray can be wiped in place (bytes are immutable)")
        data[:] = bytes(len(data))
        return data.count(0) == len(data)