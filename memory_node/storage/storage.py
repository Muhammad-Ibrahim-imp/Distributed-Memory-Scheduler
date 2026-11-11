from common.types.errors import ObjectNotFoundError


class ObjectStore:
    """Holds each object's bytes. Buffers are bytearray so SecureDeleter can wipe them in place."""

    def __init__(self):
        self._data: dict[str, bytearray] = {}

    def put(self, object_id: str, data: bytes) -> None:
        """Stores a copy. Replaces any existing object (full replace, confirmed by A2).
        Week 3: write() should wipe the old buffer before replacing it (open item)."""
        self._data[object_id] = bytearray(data)

    def get(self, object_id: str) -> bytes:
        try:
            return bytes(self._data[object_id])
        except KeyError:
            raise ObjectNotFoundError(f"object {object_id} not found") from None

    def has(self, object_id: str) -> bool:
        return object_id in self._data

    def delete(self, object_id: str) -> bytearray:
        """Removes the object and returns its buffer, so the caller can wipe it."""
        try:
            return self._data.pop(object_id)
        except KeyError:
            raise ObjectNotFoundError(f"object {object_id} not found") from None
