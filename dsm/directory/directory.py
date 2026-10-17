# raised by bump_version if the object is unknown
from common.types.errors import ObjectNotFoundError
import asyncio
from dataclasses import dataclass
from enum import Enum


class ObjectState(str, Enum):
    # object exists and its node is reachable and trusted, reads and writes allowed
    ALLOCATED = "ALLOCATED"
    # node is QUARANTINED, reads are allowed but writes must raise ObjectUnavailableError
    QUARANTINED = "QUARANTINED"
    # node is REVOKED, reads and writes must both raise ObjectUnavailableError
    REVOKED = "REVOKED"


@dataclass
class ObjectRecord:
    # opaque UUID4 string created by the Object Manager at alloc (nobody else parses it)
    object_id: str
    # client identity that owns this object (passed as owner_identity to check_access)
    owner_identity: str
    # allocated size in bytes
    size: int
    # which memory node holds the bytes (chosen by B2's scheduler)
    node_id: str
    # bumped on every write; versioning is A2's job, not A1's node
    version: int = 0
    state: ObjectState = ObjectState.ALLOCATED


class Directory:
    """Global object namespace with a lock for each object's metadata."""

    def __init__(self) -> None:
        self._records: dict[str, ObjectRecord] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, object_id: str) -> ObjectRecord | None:
        return self._records.get(object_id)

    def put(self, record: ObjectRecord) -> None:
        if record.object_id not in self._locks:
            self._locks[record.object_id] = asyncio.Lock()
        self._records[record.object_id] = record

    def get_lock(self, object_id: str) -> asyncio.Lock | None:
        return self._locks.get(object_id)

    def remove(self, object_id: str) -> None:
        # forget an object, silent if it is already gone so free stays idempotent
        self._records.pop(object_id, None)
        # drop the lock too, a coroutine that already holds it keeps its own reference
        self._locks.pop(object_id, None)

    def bump_version(self, object_id: str) -> int:
        # called after a successful node write, returns the new version number
        record = self._records.get(object_id)
        if record is None:
            raise ObjectNotFoundError(object_id)   # callers should have checked, so this is a bug guard
        record.version += 1
        return record.version

    def mark_node_state(self, node_id: str, state: ObjectState) -> list[str]:
        # set the state of every object on a node and return their ids
        # the revocation handler calls this with REVOKED or QUARANTINED
        # calling it with ALLOCATED restores them if the node becomes trusted again
        affected = []
        for record in self._records.values():
            if record.node_id == node_id:
                record.state = state               # same record object, so version and lock are kept
                affected.append(record.object_id)
        return affected