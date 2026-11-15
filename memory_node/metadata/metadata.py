from dataclasses import dataclass

from common.types.errors import InvalidRequestError, ObjectNotFoundError


@dataclass
class ObjectMetadata:
    object_id: str
    owner: str
    size: int
    created_at: float
    sha256: str | None = None   # None until the first write()


class MetadataStore:
    def __init__(self):
        self._records: dict[str, ObjectMetadata] = {}

    def add(self, record: ObjectMetadata) -> None:
        if record.object_id in self._records:
            # [Open] alloc on an existing object_id is undefined: asked A2.
            raise InvalidRequestError(f"object {record.object_id} already exists")
        self._records[record.object_id] = record

    def get(self, object_id: str) -> ObjectMetadata:
        try:
            return self._records[object_id]
        except KeyError:
            raise ObjectNotFoundError(f"object {object_id} not found") from None

    def has(self, object_id: str) -> bool:
        return object_id in self._records

    def set_sha256(self, object_id: str, sha256: str) -> None:
        self.get(object_id).sha256 = sha256

    def remove(self, object_id: str) -> ObjectMetadata:
        try:
            return self._records.pop(object_id)
        except KeyError:
            raise ObjectNotFoundError(f"object {object_id} not found") from None

    def count(self) -> int:
        return len(self._records)