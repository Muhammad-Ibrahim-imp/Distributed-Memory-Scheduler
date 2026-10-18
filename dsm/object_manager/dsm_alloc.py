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
from dataclasses import dataclass
from typing import Awaitable, Protocol, TypeVar
from uuid import uuid4

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
from dsm.directory.directory import Directory, ObjectRecord, ObjectState

_NodeResult = TypeVar("_NodeResult")


class Scheduler(Protocol):
    """Synchronous scheduler interface used for placement decisions."""

    def select_node_from_state(
        self,
        object_id: str,
        object_size: int,
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

    async def _call_node(self, coro: Awaitable[_NodeResult]) -> _NodeResult:
        """Run a node operation with a deadline and normalize timeout errors."""
        try:
            return await asyncio.wait_for(coro, timeout=self.node_timeout)
        except asyncio.TimeoutError as exc:
            raise DSMTimeoutError("Node operation timed out.") from exc

    async def alloc(
        self,
        ctx: RequestContext,
        owner_identity: str,
        size: int,
    ) -> str:
        """Allocate an object on a selected node and return its UUID4."""
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise InvalidRequestError("size must be a positive integer")

        object_id = str(uuid4())
        last_error: OutOfCapacityError | DSMTimeoutError | None = None

        for _ in range(self.config.MAX_ALLOC_ATTEMPTS):
            try:
                decision = self.scheduler.select_node_from_state(
                    object_id=object_id,
                    object_size=size,
                )
            except RuntimeError as exc:
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
            except OutOfCapacityError as exc:
                last_error = exc
                continue
            except QuotaExceededError:
                raise
            except DSMTimeoutError as exc:
                try:
                    await self._call_node(
                        self.node_client.free(
                            ctx,
                            decision.node_id,
                            object_id,
                            owner_identity,
                        )
                    )
                except Exception:
                    # A timed-out allocation may have reserved space; cleanup must not block retry.
                    pass
                last_error = exc
                continue

            self.directory.put(
                ObjectRecord(
                    object_id=object_id,
                    owner_identity=owner_identity,
                    size=size,
                    node_id=decision.node_id,
                    version=0,
                    state=ObjectState.ALLOCATED,
                )
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

        if record.state is ObjectState.REVOKED:
            raise ObjectUnavailableError("object is on a revoked node")

        return await self._call_node(
            self.node_client.read(
                ctx,
                record.node_id,
                object_id,
                record.owner_identity,
            )
        )

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
            if record.state in (ObjectState.QUARANTINED, ObjectState.REVOKED):
                raise ObjectUnavailableError("object is not writable in its current state")
            if len(data) > record.size:
                raise InvalidRequestError("data exceeds the allocated size")

            await self._call_node(
                self.node_client.write(
                    ctx,
                    record.node_id,
                    object_id,
                    record.owner_identity,
                    bytes(data),
                )
            )
            return self.directory.bump_version(object_id)

    async def free(
        self,
        ctx: RequestContext,
        caller_identity: str,
        object_id: str,
    ) -> None:
        """Free an owned object, keeping metadata if node deletion times out."""
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
            if record.state is not ObjectState.REVOKED:
                await self._call_node(
                    self.node_client.free(
                        ctx,
                        record.node_id,
                        object_id,
                        record.owner_identity,
                    )
                )

            self.directory.remove(object_id)