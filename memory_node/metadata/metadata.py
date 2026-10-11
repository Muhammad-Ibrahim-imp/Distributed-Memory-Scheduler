from dataclasses import dataclass


@dataclass
class ObjectMetadata:
    object_id: str
    owner: str
    size: int
    created_at: float
    sha256: str | None = None   # None until the first write(), since alloc() has no data yet


class MetadataStore:
    def __init__(self):
        self._records: dict[str, ObjectMetadata] = {}

    def add(self, record: ObjectMetadata) -> None:
        raise NotImplementedError

    def get(self, object_id: str) -> ObjectMetadata:
        raise NotImplementedError

    def remove(self, object_id: str) -> None:
        raise NotImplementedError

    def count(self) -> int:
        return len(self._records)