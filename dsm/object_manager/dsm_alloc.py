# common/interfaces/node_client.py
# the contract between A2's Object Manager and B1's network code
# A2 calls these methods, B1 builds the Envelope inside them and sends it

from typing import Protocol


class NodeClient(Protocol):
    # reserve space on the node, raises OutOfCapacityError if it will not fit
    async def alloc(self, ctx, node_id, object_id, owner_identity, size) -> None: ...

    # return the stored bytes, raises ObjectNotFoundError for unknown id
    async def read(self, ctx, node_id, object_id, owner_identity) -> bytes: ...

    # replace the whole value, raises ObjectNotFoundError if never allocated
    async def write(self, ctx, node_id, object_id, owner_identity, data: bytes) -> None: ...

    # delete the object, silent no-op if it does not exist
    async def free(self, ctx, node_id, object_id, owner_identity) -> None: ...
"""Object allocation, access, mutation, and release for the DSM runtime.

Writes to one object are serialized by that object's Directory lock; writes
to different objects can run in parallel. A read after a completed write sees
the new data, while a read concurrent with a write may see either value.
"""

import asyncio
from dataclasses import dataclass, replace
from typing import Awaitable, Protocol, TypeVar
from uuid import UUID, uuid4

from common.interfaces.NodeClient import NodeClient
from common.types.context import RequestContext
from common.types.errors import (
    DSMTimeoutError,
    InvalidRequestError,
    NotOwnerError,
    ObjectNotFoundError,
    ObjectUnavailableError,
    OutOfCapacityError,
    QuotaExceededError,
)
from common.types.placement import PlacementDecision
from common.types.security import TrustState
from dsm.cache import ObjectCache
from dsm.directory.directory import Directory, ObjectRecord, ObjectState

_NodeResult = TypeVar("_NodeResult")

# SUSPICIOUS currently preserves existing objects as ALLOCATED.
TRUST_TO_OBJECT_STATE: dict[TrustState, ObjectState] = {
    TrustState.TRUSTED: ObjectState.ALLOCATED,
    TrustState.SUSPICIOUS: ObjectState.ALLOCATED,
    TrustState.QUARANTINED: ObjectState.QUARANTINED,
    TrustState.REVOKED: ObjectState.UNAVAILABLE,
}


class Scheduler(Protocol):
    """Synchronous scheduler interface used for placement decisions."""

    def select_node_from_state(
        self,
        object_id: str,
        object_size: int,
        excluded_node_ids: set[str] | None = None,
    ) -> PlacementDecision:
        """Return the node selected for an object."""


class AccessChecker(Protocol):
    """Authorization interface implemented by the security component."""

    def check_access(
        self,
        identity: str,
        owner_identity: str,
        action: str,
        object_id: str,
    ) -> bool:
        """Return whether an identity may perform an action on an object."""

    def get_trust_state(self, node_id: str) -> TrustState:
        """Return the node's current trust state."""


@dataclass(frozen=True)
class ObjectManagerConfig:
    """Configuration for Object Manager operations."""

    MAX_ALLOC_ATTEMPTS: int = 3


class ObjectManager:
    """Manage global object allocation and freeing across memory nodes."""

    def __init__(
        self,
        directory: Directory,
        node_client: NodeClient,
        scheduler: Scheduler,
        security: AccessChecker,
        config: ObjectManagerConfig = ObjectManagerConfig(),
        node_timeout: float = 5.0,
        cache: ObjectCache | None = None,
        coordinator_generation: str | None = None,
    ) -> None:
        """Create an Object Manager with its runtime dependencies."""
        if (
            not isinstance(config.MAX_ALLOC_ATTEMPTS, int)
            or isinstance(config.MAX_ALLOC_ATTEMPTS, bool)
            or config.MAX_ALLOC_ATTEMPTS <= 0
        ):
            raise ValueError("MAX_ALLOC_ATTEMPTS must be a positive integer.")
        if (
            not isinstance(node_timeout, (int, float))
            or isinstance(node_timeout, bool)
            or node_timeout <= 0
        ):
            raise ValueError("node_timeout must be a positive number.")

        self.directory = directory
        self.node_client = node_client
        self.scheduler = scheduler
        self.security = security
        self.config = config
        self.node_timeout = float(node_timeout)
        self.cache = cache
        if coordinator_generation is not None:
            generation_changed = (
                coordinator_generation != directory.coordinator_generation
            )
            directory.activate_generation(coordinator_generation)
            if generation_changed and self.cache is not None:
                self.cache.clear()
        self.coordinator_generation = directory.coordinator_generation
        self._revoked_node_timestamps: dict[str, float] = {}

    def start_generation(self, coordinator_generation: str) -> list[str]:
        """Activate a coordinator generation and invalidate all prior local state."""
        stale_object_ids = self.directory.activate_generation(coordinator_generation)
        if coordinator_generation != self.coordinator_generation:
            self.coordinator_generation = coordinator_generation
            self._revoked_node_timestamps.clear()
            if self.cache is not None:
                self.cache.clear()
        return stale_object_ids

    def _generation_context(self, ctx: RequestContext) -> RequestContext:
        """Attach the active generation or reject a request from a stale runtime."""
        if (
            ctx.coordinator_generation is not None
            and ctx.coordinator_generation != self.coordinator_generation
        ):
            raise ObjectUnavailableError(
                "request belongs to an obsolete coordinator generation"
            )
        return replace(ctx, coordinator_generation=self.coordinator_generation)

    def _ensure_current_object_id(self, object_id: str) -> None:
        """Reject generation-qualified handles issued by an earlier coordinator."""
        generation, separator, nonce = object_id.rpartition(".")
        if not separator:
            return
        try:
            UUID(nonce)
        except ValueError:
            return
        if generation != self.coordinator_generation:
            raise ObjectNotFoundError(object_id)

    async def _call_node(self, coro: Awaitable[_NodeResult]) -> _NodeResult:
        """Run a node operation with a deadline and normalize timeout errors."""
        try:
            return await asyncio.wait_for(coro, timeout=self.node_timeout)
        except asyncio.TimeoutError as exc:
            raise DSMTimeoutError("Node operation timed out.") from exc

    def handle_node_trust_state_changed(
        self,
        node_id: str,
        trust_state: TrustState,
        timestamp: float,
    ) -> list[str]:
        """Apply a node trust transition and return the affected object ids."""
        object_state = TRUST_TO_OBJECT_STATE[trust_state]
        if trust_state is TrustState.REVOKED:
            return self.handle_node_revoked(node_id, timestamp)
        if node_id in self._revoked_node_timestamps:
            return []

        affected = self.directory.mark_node_state(node_id, object_state)
        return affected

    def handle_node_revoked(self, node_id: str, timestamp: float) -> list[str]:
        """Mark objects unavailable after M5 has closed the node connection."""
        self._revoked_node_timestamps.setdefault(node_id, timestamp)
        revoked_at = self._revoked_node_timestamps[node_id]
        affected = self.directory.mark_node_unavailable(node_id, revoked_at)
        if self.cache is not None:
            self.cache.invalidate_many(affected)
        return affected

    def handle_node_failure(self, node_id: str) -> list[str]:
        """Mark a failed node's objects unavailable and evict their cache entries."""
        if node_id in self._revoked_node_timestamps:
            return []
        affected = self.directory.mark_node_failed(node_id)
        if self.cache is not None:
            self.cache.invalidate_many(affected)
        return affected

    async def alloc(
        self,
        ctx: RequestContext,
        owner_identity: str,
        size: int,
    ) -> str:
        """Allocate an object and return its generation-qualified UUID4."""
        ctx = self._generation_context(ctx)
        generation = self.coordinator_generation
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise InvalidRequestError("size must be a positive integer")

        object_id = f"{generation}.{uuid4()}"
        attempted_node_ids: set[str] = set()
        last_error: (
            OutOfCapacityError | QuotaExceededError | DSMTimeoutError | None
        ) = None

        for _ in range(self.config.MAX_ALLOC_ATTEMPTS):
            try:
                decision = self.scheduler.select_node_from_state(
                    object_id=object_id,
                    object_size=size,
                    excluded_node_ids=attempted_node_ids,
                )
            except RuntimeError as exc:
                if last_error is not None:
                    raise last_error
                raise OutOfCapacityError(str(exc)) from exc
            except ValueError as exc:
                raise InvalidRequestError(str(exc)) from exc

            try:
                await self._call_node(
                    self.node_client.alloc(
                        ctx,
                        decision.node_id,
                        object_id,
                        owner_identity,
                        size,
                    )
                )
            except (OutOfCapacityError, QuotaExceededError) as exc:
                attempted_node_ids.add(decision.node_id)
                last_error = exc
                continue
            except DSMTimeoutError as exc:
                await self._call_node(
                    self.node_client.free(
                        ctx,
                        decision.node_id,
                        object_id,
                        owner_identity,
                    )
                )
                attempted_node_ids.add(decision.node_id)
                last_error = exc
                continue

            if generation != self.coordinator_generation:
                raise ObjectUnavailableError(
                    "coordinator generation changed during allocation"
                )
            self.directory.put(
                ObjectRecord(
                    object_id=object_id,
                    owner_identity=owner_identity,
                    size=size,
                    node_id=decision.node_id,
                    version=0,
                    state=ObjectState.ALLOCATED,
                    coordinator_generation=generation,
                )
            )
            if decision.node_id in self._revoked_node_timestamps:
                # A placement already in flight must not escape a completed revocation.
                self.directory.mark_node_unavailable(
                    decision.node_id,
                    self._revoked_node_timestamps[decision.node_id],
                )
            return object_id

        if last_error is None:
            raise RuntimeError("Allocation attempts ended without a node result.")
        raise last_error

    async def read(
        self,
        ctx: RequestContext,
        caller_identity: str,
        object_id: str,
    ) -> bytes:
        """Read an owned object; quarantined nodes remain readable."""
        self._ensure_current_object_id(object_id)
        ctx = self._generation_context(ctx)
        generation = self.coordinator_generation
        record = self.directory.get(object_id)
        if record is None:
            raise ObjectNotFoundError(object_id)

        if not self.security.check_access(
            caller_identity,
            record.owner_identity,
            "read",
            object_id,
        ):
            raise NotOwnerError("caller is not the object owner")

        if (
            record.state in (ObjectState.UNAVAILABLE, ObjectState.REVOKED)
            or self.security.get_trust_state(record.node_id) is TrustState.REVOKED
        ):
            raise ObjectUnavailableError("object is on an unavailable node")

        if self.cache is not None:
            cached_entry = self.cache.get(object_id)
            if cached_entry is not None and self.directory.is_current(
                object_id,
                cached_entry[0],
            ):
                if generation != self.coordinator_generation:
                    raise ObjectUnavailableError(
                        "coordinator generation changed during read"
                    )
                return cached_entry[1]

        # Capture before the await so delayed readers cannot stamp a newer version.
        version_before = record.version
        return await self._call_node(
            self._read_and_cache(
                ctx,
                record,
                object_id,
                version_before,
                generation,
            )
        )

    async def _read_and_cache(
        self,
        ctx: RequestContext,
        record: ObjectRecord,
        object_id: str,
        version_before: int,
        generation: str,
    ) -> bytes:
        """Fetch bytes from a node and cache them at the captured version."""
        data = await self.node_client.read(
            ctx,
            record.node_id,
            object_id,
            record.owner_identity,
        )
        if generation != self.coordinator_generation:
            raise ObjectUnavailableError("coordinator generation changed during read")
        if self.cache is not None:
            self.cache.put(object_id, version_before, data)
        return data

    async def write(
        self,
        ctx: RequestContext,
        caller_identity: str,
        object_id: str,
        data: bytes | bytearray,
    ) -> int:
        """Replace an object's bytes and return its next version.

        After a timeout, the result is unknown: the node may or may not have
        applied the write, but the Directory version is not incremented.
        """
        self._ensure_current_object_id(object_id)
        ctx = self._generation_context(ctx)
        generation = self.coordinator_generation
        record = self.directory.get(object_id)
        if record is None:
            raise ObjectNotFoundError(object_id)

        if not self.security.check_access(
            caller_identity,
            record.owner_identity,
            "write",
            object_id,
        ):
            raise NotOwnerError("caller is not the object owner")

        if not isinstance(data, (bytes, bytearray)) or len(data) > record.size:
            raise InvalidRequestError("data must be bytes within the allocated size")

        lock = self.directory.get_lock(object_id)
        if lock is None:
            raise ObjectNotFoundError(object_id)

        async with lock:
            record = self.directory.get(object_id)
            if record is None:
                raise ObjectNotFoundError(object_id)
            if record.state in (
                ObjectState.UNAVAILABLE,
                ObjectState.QUARANTINED,
                ObjectState.REVOKED,
            ):
                raise ObjectUnavailableError("object is not writable in its current state")
            trust_state = self.security.get_trust_state(record.node_id)
            if trust_state in (TrustState.QUARANTINED, TrustState.REVOKED):
                raise ObjectUnavailableError(
                    "object is not writable in its node's current trust state"
                )
            if len(data) > record.size:
                raise InvalidRequestError("data exceeds the allocated size")

            try:
                await self._call_node(
                    self.node_client.write(
                        ctx,
                        record.node_id,
                        object_id,
                        record.owner_identity,
                        bytes(data),
                    )
                )
                if generation != self.coordinator_generation:
                    raise ObjectUnavailableError(
                        "coordinator generation changed during write"
                    )
            except BaseException:
                if self.cache is not None:
                    self.cache.invalidate(object_id)
                raise

            new_version = self.directory.bump_version(object_id)
            if self.cache is not None:
                self.cache.put(object_id, new_version, bytes(data))
            return new_version

    async def free(
        self,
        ctx: RequestContext,
        caller_identity: str,
        object_id: str,
    ) -> None:
        """Free an owned object, keeping metadata if node deletion times out."""
        self._ensure_current_object_id(object_id)
        ctx = self._generation_context(ctx)
        record = self.directory.get(object_id)
        if record is None:
            return

        if not self.security.check_access(
            caller_identity,
            record.owner_identity,
            "free",
            object_id,
        ):
            raise NotOwnerError("caller is not the object owner")

        lock = self.directory.get_lock(object_id)
        if lock is None:
            return

        async with lock:
            record = self.directory.get(object_id)
            if record is None:
                return

            # Assumption pending M5 confirmation: an authorized free is allowed in every state.
            is_revoked = (
                record.state is ObjectState.REVOKED
                or record.node_id in self._revoked_node_timestamps
                or self.security.get_trust_state(record.node_id) is TrustState.REVOKED
            )
            if not is_revoked:
                try:
                    await self._call_node(
                        self.node_client.free(
                            ctx,
                            record.node_id,
                            object_id,
                            record.owner_identity,
                        )
                    )
                except BaseException:
                    if self.cache is not None:
                        self.cache.invalidate(object_id)
                    raise

            self.directory.remove(object_id)
            if self.cache is not None:
                self.cache.invalidate(object_id)