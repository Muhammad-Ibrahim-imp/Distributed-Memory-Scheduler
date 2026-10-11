import asyncio

from memory_node.allocator.allocator import Allocator
from memory_node.storage.storage import ObjectStore
from memory_node.metadata.metadata import MetadataStore, ObjectMetadata
from memory_node.security.isolation import IsolationChecker
from memory_node.security.quotas import QuotaTracker
from memory_node.security.integrity import compute_sha256, verify_integrity
from memory_node.security.secure_delete import SecureDeleter
from memory_node.security.audit import SecurityAuditReporter
from memory_node.monitor import NodeResourceMonitor, ResourceStats, CapacityStats

from common.types.errors import (
    ObjectNotFoundError, NotOwnerError, ObjectUnavailableError,
    OutOfCapacityError, InvalidRequestError, IntegrityError,
    QuotaExceededError,
)
from common.types.node import NodeStats
from common.types.context import RequestContext   # path unconfirmed, verify after pull


class MemoryNode:
    """A1 Node API. Signatures are frozen per B1's spec.

    Check order inside read/write/free (recommendation):
      1. missing object -> ObjectNotFoundError
      2. wrong owner    -> report AUTHZ_DENIED, raise NotOwnerError
      3. unavailable    -> ObjectUnavailableError
    This stops a non-owner learning that someone else's object is locked.
    """

    def __init__(self, node_id: str, total_capacity_bytes: int):
        self.node_id = node_id
        self.allocator = Allocator(total_capacity_bytes)
        self.storage = ObjectStore()
        self.metadata = MetadataStore()
        self.isolation = IsolationChecker()
        self.quotas = QuotaTracker()
        self.secure_deleter = SecureDeleter()
        self.monitor = NodeResourceMonitor(node_id)
        self.audit = SecurityAuditReporter(node_id)

        # Requests can arrive concurrently over B1's single connection.
        self._object_locks: dict[str, asyncio.Lock] = {}
        # Objects blocked by migration or degraded storage (B1's cases 2 and 3).
        # Nothing sets this yet.
        self._unavailable: set[str] = set()

    # ---- helpers ----
    def _lock_for(self, object_id: str) -> asyncio.Lock:
        # TODO: remove the lock entry when the object is freed
        return self._object_locks.setdefault(object_id, asyncio.Lock())

    def _check_available(self, object_id: str) -> None:
        """Object exists but can't be accessed right now."""
        if object_id in self._unavailable or self._lock_for(object_id).locked():
            raise ObjectUnavailableError(f"{object_id} temporarily unavailable")

    # ---- Called by B1 on behalf of A2 (frozen) ----
    async def alloc(self, ctx: RequestContext, object_id: str,
                    owner_identity: str, size: int) -> None:
        """Reserve space only. write() stores the bytes.
        Planned order: validate size (InvalidRequestError) -> quota check
        (QuotaExceededError) -> allocator.reserve (OutOfCapacityError)
        -> add metadata (sha256 not known yet) -> record quota usage.
        Open: what if object_id already exists? Not defined, ask A2."""
        raise NotImplementedError

    async def write(self, ctx: RequestContext, object_id: str,
                    owner_identity: str, data: bytes) -> None:
        """Full replace only (A2 confirmed).
        Planned order: found -> owner -> available -> len(data) <= allocated
        size (recommendation) -> store -> compute and save sha256."""
        raise NotImplementedError

    async def read(self, ctx: RequestContext, object_id: str,
                   owner_identity: str) -> bytes:
        """Planned order: found -> owner -> available -> get bytes
        -> verify sha256. On mismatch report TAMPER_DETECTED, raise IntegrityError.
        Open: read of an allocated but never-written object is undefined, ask A2."""
        raise NotImplementedError

    async def free(self, ctx: RequestContext, object_id: str,
                   owner_identity: str) -> None:
        """Planned order: found -> owner -> available -> wipe -> delete bytes
        -> allocator.release -> remove metadata -> update quota usage.
        Open: if wipe fails (SECURE_DELETE_FAILED), does free still succeed? Ask M5."""
        raise NotImplementedError

    # ---- Called by B2 ----
    async def get_resource_state(self, node_id: str) -> ResourceStats:
        return self.monitor.get_resource_state(node_id)

    async def get_capacity_stats(self, node_id: str) -> CapacityStats:
        return self.monitor.get_capacity_stats(node_id)

    # ---- Called by B1 ----
    async def get_stats(self) -> NodeStats:
        return self.monitor.get_node_stats(object_count=self.metadata.count())