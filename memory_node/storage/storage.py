class ObjectStore:
    """Holds raw bytes for each object, keyed by object_id."""

    def __init__(self):
        self._data: dict[str, bytes] = {}

    def put(self, object_id: str, data: bytes) -> None:
        raise NotImplementedError

    def get(self, object_id: str) -> bytes:
        raise NotImplementedError

    def delete(self, object_id: str) -> None:
        raise NotImplementedError