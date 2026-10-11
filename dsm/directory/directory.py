from dataclasses import dataclass
from enum import Enum


class ObjectState(str, Enum):
    # object exists and its node is reachable and trusted
    ALLOCATED = "ALLOCATED"
    # node was REVOKED; reads and writes must raise ObjectUnavailableError
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class ObjectRecord:
    # opaque UUID4 string created by the Object Manager at alloc (nobody else parses it)
    object_id: str
    # client identity that owns this object (passed as owner_identity to check_access)
    owner: str
    # allocated size in bytes
    size: int
    # which memory node holds the bytes (chosen by B2's scheduler)
    node_id: str
    # bumped on every write; versioning is A2's job, not A1's node
    version: int = 0
    state: ObjectState = ObjectState.ALLOCATED


class Directory:
    """Global object namespace: object_id -> ObjectRecord. Week 2 adds the real logic."""

    def __init__(self) -> None:
        # plain in-memory dict is enough for the prototype
        self._records: dict[str, ObjectRecord] = {}

    def get(self, object_id: str) -> ObjectRecord | None:
        # None if unknown; the caller decides which error to raise
        return self._records.get(object_id)

    def put(self, record: ObjectRecord) -> None:
        # insert or replace a record
        self._records[record.object_id] = record