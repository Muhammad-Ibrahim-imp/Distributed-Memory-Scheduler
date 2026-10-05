class SecureDeleter:
    """Overwrites/clears object bytes before releasing memory."""

    def wipe(self, data: bytearray) -> None:
        raise NotImplementedError