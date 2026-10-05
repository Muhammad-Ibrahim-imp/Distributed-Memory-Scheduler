from memory_node.allocator.allocator import Allocator
from memory_node.storage.storage import ObjectStore
from memory_node.metadata.metadata import MetadataStore, ObjectMetadata
from memory_node.security.isolation import IsolationChecker
from memory_node.security.quotas import QuotaTracker
from memory_node.security.integrity import compute_sha256, verify_integrity
from memory_node.security.secure_delete import SecureDeleter
from memory_node.security.audit import SecurityAuditReporter
from memory_node.monitor import NodeResourceMonitor, ResourceStats

from common.types.errors import (
    ObjectNotFoundError, NotOwnerError, ObjectUnavailableError,
    OutOfCapacityError, InvalidRequestError, IntegrityError,
)
from common.types.node import NodeStats            # shared type from B2, confirmed by B1
from common.types.context import RequestContext     # [Open] confirm exact path with B1


class MemoryNode:
    """The A1 Node API. Signatures below are frozen per B1's spec."""

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

    # ---- Called by B1, on behalf of A2 (frozen) ----
    async def alloc(self, ctx: RequestContext, object_id: str, owner_identity: str, size: int) -> None:
        """Reserve space only. No bytes stored yet — write() does that."""
        raise NotImplementedError

    async def write(self, ctx: RequestContext, object_id: str, owner_identity: str, data: bytes) -> None:
        raise NotImplementedError

    async def read(self, ctx: RequestContext, object_id: str, owner_identity: str) -> bytes:
        raise NotImplementedError

    async def free(self, ctx: RequestContext, object_id: str, owner_identity: str) -> None:
        raise NotImplementedError

    # ---- Called by B2 (confirmed) ----
    async def get_resource_state(self, node_id: str) -> ResourceStats:
        return self.monitor.get_resource_state(node_id)

    # ---- Called by B1 (uses shared NodeStats) ----
    async def get_stats(self) -> NodeStats:
        return self.monitor.get_node_stats(object_count=self.metadata.count())