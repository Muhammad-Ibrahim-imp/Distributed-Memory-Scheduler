import asyncio
import logging
import time

from memory_node.allocator.allocator import Allocator
from memory_node.storage.storage import ObjectStore
from memory_node.metadata.metadata import MetadataStore, ObjectMetadata
from memory_node.security.isolation import IsolationChecker
from memory_node.security.quotas import QuotaTracker
from memory_node.security.integrity import compute_sha256, verify_integrity
from memory_node.security.secure_delete import SecureDeleter
from memory_node.security.audit import SecurityAuditReporter
from memory_node.monitor import NodeResourceMonitor, CapacityStats, NodeReport

from common.types.errors import (
    NotOwnerError, ObjectUnavailableError, OutOfCapacityError,
    InvalidRequestError, IntegrityError, QuotaExceededError,
)
from common.types.resource import ResourceStats
from common.types.context import RequestContext
from typing import Callable
from common.types.security import SecurityEvent
logger = logging.getLogger(__name__)

class MemoryNode:
    """A1 Node API. Signatures are frozen per B1's spec.

    Check order in read/write/free: object exists -> requester owns it -> not busy.
    A missing object raises ObjectNotFoundError (free: returns silently), and a
    non-owner never learns whether someone else's object is busy.

    Operations contain no awaits, so under asyncio each one runs atomically. The
    per-object lock and the _unavailable set make the "temporarily unavailable"
    rule real, but nothing triggers them in normal use until an await is added
    (for example real I/O, or async delivery of audit events).
    """

    def __init__(self, node_id: str, total_capacity_bytes: int,
                 max_memory_fraction_per_client: float = 0.5,
                 max_objects_per_client: int | None = 10_000,
                 event_sink: Callable[[SecurityEvent], None] | None = None):

        self.node_id = node_id
        self.allocator = Allocator(total_capacity_bytes)
        self.storage = ObjectStore()
        self.metadata = MetadataStore()
        self.isolation = IsolationChecker()
        # Defaults mirror configs/security.yaml (M5). B1's node_main.py should pass
        # the real values from that file. The capacity experiment overrides them.
        self.quotas = QuotaTracker.from_fraction(
            total_capacity_bytes, max_memory_fraction_per_client,
            max_objects_per_client)
        self.secure_deleter = SecureDeleter()
        self.monitor = NodeResourceMonitor(node_id, self.allocator)
        self.audit = SecurityAuditReporter(node_id, event_sink)

        effective_total, _ = self.monitor.effective_memory()
        if total_capacity_bytes > effective_total:
            logger.warning(
                "pool capacity %d bytes exceeds the %d bytes this process can use "
                "(container limit or host RAM); free_ram would overstate what can "
                "really be allocated", total_capacity_bytes, effective_total)

        self._object_locks: dict[str, asyncio.Lock] = {}
        # Objects blocked by migration or degraded storage. Nothing sets this yet.
        self._unavailable: set[str] = set()

    # ---- helpers ----
    @staticmethod
    def _require_text(value, name: str) -> None:
        if not isinstance(value, str) or not value:
            raise InvalidRequestError(f"{name} must be a non-empty string")

    def _lock_for(self, object_id: str) -> asyncio.Lock:
        """Only call for objects that exist (or alloc), so probing random ids
        can't grow the dict."""
        return self._object_locks.setdefault(object_id, asyncio.Lock())

    def _check_available(self, object_id: str) -> None:
        lock = self._object_locks.get(object_id)
        if object_id in self._unavailable or (lock is not None and lock.locked()):
            raise ObjectUnavailableError("object temporarily unavailable")

    def _report(self, report_fn, *args) -> None:
        """Reporting must never change the result the client sees."""
        try:
            report_fn(*args)
        except Exception:
            logger.exception("could not report security event")

    def _check_owner(self, ctx: RequestContext, meta: ObjectMetadata,
                     owner_identity: str) -> None:
        try:
            self.isolation.check_access(meta.owner, owner_identity)
        except NotOwnerError:
            self._report(self.audit.report_authz_denied, owner_identity,
                         ctx.request_id, "not_owner", meta.object_id)
            raise

    def _wipe(self, ctx: RequestContext, object_id: str, owner_identity: str,
              buffer: bytearray) -> None:
        """A failed wipe is reported but never blocks the operation (M5)."""
        try:
            ok = self.secure_deleter.wipe(buffer)
        except Exception:
            ok = False
        if not ok:
            self._report(self.audit.report_secure_delete_failed, object_id,
                         ctx.request_id, owner_identity)

    def update_node_identity(self, new_id: str) -> None:
        """Called by B1's NodeService after registration or re-registration (M5):
        every registration issues a fresh identity while the node keeps running with
        its objects. Synchronous on purpose: do NOT await it. Updates the identity used
        for event actor_id, heartbeat reports and resource-state checks."""
        self._require_text(new_id, "node identity")
        self.node_id = new_id
        self.monitor.node_id = new_id
        self.audit.node_id = new_id

    # ---- Called by B1 on behalf of A2 (frozen) ----
    async def alloc(self, ctx: RequestContext, object_id: str,
                    owner_identity: str, size: int) -> None:
        """The only way an object comes into existence (A2). Quota and capacity errors
        are raised here, nothing is deferred to write.
        Idempotent for retries after a timeout (A2): the same id, owner and size is a
        no-op success and never double-counts quota or capacity. The same id with a
        different owner or size raises InvalidRequestError.
        Order: validate -> existing? -> quota -> capacity -> record."""
        self._require_text(object_id, "object_id")
        self._require_text(owner_identity, "owner_identity")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise InvalidRequestError("size must be a positive integer")

        lock = self._lock_for(object_id)
        try:
            async with lock:
                if self.metadata.has(object_id):
                    existing = self.metadata.get(object_id)
                    if existing.owner == owner_identity and existing.size == size:
                        return
                    # The message must not reveal the existing owner or size.
                    raise InvalidRequestError(
                        "object already exists with different parameters")
                try:
                    self.quotas.check_within_quota(owner_identity, size)
                except QuotaExceededError:
                    self._report(self.audit.report_authz_denied, owner_identity,
                                 ctx.request_id, "quota", object_id)
                    raise
                if not self.allocator.reserve(size):
                    raise OutOfCapacityError("node is out of capacity")
                self.metadata.add(ObjectMetadata(
                    object_id=object_id, owner=owner_identity, size=size,
                    created_at=time.time()))
                self.quotas.record_alloc(owner_identity, size)
        finally:
            if not self.metadata.has(object_id) and not lock.locked():
                self._object_locks.pop(object_id, None)

    async def write(self, ctx: RequestContext, object_id: str,
                    owner_identity: str, data: bytes) -> None:
        """Full replace of an allocated object. Unknown id raises ObjectNotFoundError
        and never creates the object (A2). Capacity was reserved at alloc, so write
        changes no accounting.
        [ASSUMED] data larger than the allocation raises InvalidRequestError (asked A2).
        [ASSUMED] the replaced buffer is wiped first; a failed wipe is reported and
        the write still succeeds."""
        self._require_text(object_id, "object_id")
        self._require_text(owner_identity, "owner_identity")
        if not isinstance(data, (bytes, bytearray)):
            raise InvalidRequestError("data must be bytes")

        meta = self.metadata.get(object_id)            # ObjectNotFoundError
        self._check_owner(ctx, meta, owner_identity)   # NotOwnerError
        self._check_available(object_id)               # ObjectUnavailableError
        if len(data) > meta.size:
            raise InvalidRequestError("data is larger than the allocated size")

        async with self._lock_for(object_id):
            if self.storage.has(object_id):
                self._wipe(ctx, object_id, owner_identity,
                           self.storage.delete(object_id))
            self.storage.put(object_id, data)
            self.metadata.set_sha256(object_id, compute_sha256(data))

    async def read(self, ctx: RequestContext, object_id: str,
                   owner_identity: str) -> bytes:
        """Returns the bytes after re-checking their SHA-256. A mismatch is reported
        as an object-integrity failure and raises IntegrityError. An object that was
        allocated but never written returns b"" (A2), not zero-filled bytes."""
        self._require_text(object_id, "object_id")
        self._require_text(owner_identity, "owner_identity")

        meta = self.metadata.get(object_id)
        self._check_owner(ctx, meta, owner_identity)
        self._check_available(object_id)
        if meta.sha256 is None:
            return b""

        async with self._lock_for(object_id):
            data = self.storage.get(object_id)
            if not verify_integrity(data, meta.sha256):
                self._report(self.audit.report_integrity_failed, object_id,
                             ctx.request_id)
                raise IntegrityError("integrity check failed")
        return data

    async def free(self, ctx: RequestContext, object_id: str,
                   owner_identity: str) -> None:
        """Idempotent (A2): an unknown or already-freed id returns silently.
        A failed wipe is reported but the free still succeeds (M5): the
        allocation and the client's quota are always released."""
        self._require_text(object_id, "object_id")
        self._require_text(owner_identity, "owner_identity")

        if not self.metadata.has(object_id):
            return
        meta = self.metadata.get(object_id)
        self._check_owner(ctx, meta, owner_identity)
        self._check_available(object_id)

        async with self._lock_for(object_id):
            if self.storage.has(object_id):    # allocated but never written: no bytes
                self._wipe(ctx, object_id, owner_identity,
                           self.storage.delete(object_id))
            self.allocator.release(meta.size)
            self.metadata.remove(object_id)
            self.quotas.record_free(meta.owner, meta.size)
        self._object_locks.pop(object_id, None)

    # ---- Called by B2 ----
    async def get_resource_state(self, node_id: str) -> ResourceStats:
        return self.monitor.get_resource_state(node_id)

    async def get_capacity_stats(self, node_id: str) -> CapacityStats:
        return self.monitor.get_capacity_stats(node_id)

    # ---- Called by B1 (heartbeat; B1 chose this option) ----
    async def get_node_report(self) -> NodeReport:
        return self.monitor.get_node_report(object_count=self.metadata.count())