# raised by bump_version if the object is unknown
from common.types.errors import ObjectNotFoundError
import asyncio
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

from dsm.consistency import ConsistencyManager


class ObjectState(str, Enum):
    # object exists and its node is reachable and trusted, reads and writes allowed
    ALLOCATED = "ALLOCATED"
    # the node is not currently reachable, so reads and writes are blocked
    UNAVAILABLE = "UNAVAILABLE"
    # node is QUARANTINED, reads are allowed but writes must raise ObjectUnavailableError
    QUARANTINED = "QUARANTINED"
    # node is REVOKED, reads and writes must both raise ObjectUnavailableError
    REVOKED = "REVOKED"


@dataclass
class ObjectRecord:
    # opaque generation-qualified UUID4 string created by the Object Manager
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
    unavailable_since: float | None = None
    coordinator_generation: str | None = None


class Directory:
    """Global object namespace with a lock for each object's metadata."""

    def __init__(self, coordinator_generation: str | None = None) -> None:
        self._coordinator_generation = self._validate_generation(
            coordinator_generation
            if coordinator_generation is not None
            else str(uuid4())
        )
        self._records: dict[str, ObjectRecord] = {}
        self._consistency = ConsistencyManager()

    @staticmethod
    def _validate_generation(generation: str) -> str:
        if not isinstance(generation, str) or not generation or "." in generation:
            raise ValueError(
                "coordinator_generation must be a non-empty string without '.'"
            )
        return generation

    @property
    def coordinator_generation(self) -> str:
        return self._coordinator_generation

    def activate_generation(self, coordinator_generation: str) -> list[str]:
        """Switch generations and discard all prior-generation metadata."""
        generation = self._validate_generation(coordinator_generation)
        if generation == self._coordinator_generation:
            return []

        stale_object_ids = list(self._records)
        self._records.clear()
        self._consistency.clear()
        self._coordinator_generation = generation
        return stale_object_ids

    def get(self, object_id: str) -> ObjectRecord | None:
        record = self._records.get(object_id)
        if (
            record is not None
            and record.coordinator_generation == self._coordinator_generation
        ):
            version = self._consistency.get_version(object_id)
            if version is not None:
                record.version = version
            return record
        return None

    def put(self, record: ObjectRecord) -> None:
        if record.coordinator_generation is None:
            record.coordinator_generation = self._coordinator_generation
        elif record.coordinator_generation != self._coordinator_generation:
            raise ValueError("object record belongs to a different coordinator generation")
        self._consistency.register(record.object_id, record.version)
        self._records[record.object_id] = record

    def get_lock(self, object_id: str) -> asyncio.Lock | None:
        return self._consistency.get_lock(object_id)

    def is_current(self, object_id: str, version: int) -> bool:
        return self._consistency.is_current(object_id, version)

    async def update_location(self, object_id: str, node_id: str) -> None:
        """Update location after data is present at the destination node."""
        lock = self.get_lock(object_id)
        if lock is None:
            raise ObjectNotFoundError(object_id)

        async with lock:
            record = self._records.get(object_id)
            if record is None:
                raise ObjectNotFoundError(object_id)
            record.node_id = node_id

    def remove(self, object_id: str) -> None:
        # forget an object, silent if it is already gone so free stays idempotent
        self._records.pop(object_id, None)
        self._consistency.forget(object_id)

    def bump_version(self, object_id: str) -> int:
        # called after a successful node write, returns the new version number
        record = self._records.get(object_id)
        if record is None:
            raise ObjectNotFoundError(object_id)
        version = self._consistency.bump_version(object_id)
        record.version = version
        return version

    def mark_node_state(self, node_id: str, state: ObjectState) -> list[str]:
        # set the state of every object on a node and return their ids
        # calling it with ALLOCATED restores objects after trust recovery
        affected = []
        for record in self._records.values():
            if record.node_id == node_id:
                record.state = state
                if state is ObjectState.ALLOCATED:
                    record.unavailable_since = None
                affected.append(record.object_id)
        return affected

    def mark_node_unavailable(self, node_id: str, timestamp: float) -> list[str]:
        """Mark every object on a revoked node unavailable at the given time."""
        affected = []
        for record in self._records.values():
            if record.node_id == node_id:
                record.state = ObjectState.UNAVAILABLE
                if record.unavailable_since is None:
                    record.unavailable_since = timestamp
                affected.append(record.object_id)
        return affected

    def mark_node_failed(self, node_id: str) -> list[str]:
        """Mark objects on a failed node unavailable without undoing revocation."""
        affected = []
        for record in self._records.values():
            if record.node_id == node_id and record.state is not ObjectState.REVOKED:
                record.state = ObjectState.UNAVAILABLE
                affected.append(record.object_id)
        return affected