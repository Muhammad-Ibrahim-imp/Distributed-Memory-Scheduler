# tests/mocks/fakes.py
# fake memory node used by A2 tests, mirrors the agreed node API
# alloc / read / write / free, ctx is always the first argument

import asyncio
from collections.abc import Callable

from common.types.errors import (
    TokenInvalidError,
    ObjectNotFoundError,
    OutOfCapacityError,
    InvalidRequestError,
)
from common.types.context import RequestContext
from common.types.placement import PlacementDecision
from common.types.security import (
    EventType,
    Result,
    SecurityEvent,
    Severity,
    TrustState,
)


class FakeNodeClient:
    """In-memory NodeClient fake with scripted failures and a complete call log."""

    def __init__(self, capacity_bytes: int = 1024) -> None:
        self.capacity = capacity_bytes   # pretend RAM limit of this node
        self.used = 0                    # bytes reserved so far
        self.reserved: dict[str, int] = {}  # object_id -> size reserved at alloc
        self.data: dict[str, bytes] = {}     # object_id -> stored bytes
        self.owners: dict[str, str] = {}     # object_id -> owner_identity
        self.fail_next_alloc: list[Exception] = []
        self.fail_next_read: list[Exception] = []
        self.fail_next_write: list[Exception] = []
        self.fail_next_free: list[Exception] = []
        self.call_log: list[tuple[str, tuple[object, ...]]] = []
        self.call_delays: dict[str, float] = {}
        self.active_write_calls = 0
        self.max_concurrent_write_calls = 0
        self.active_write_calls_by_object: dict[str, int] = {}
        self.max_concurrent_write_calls_by_object: dict[str, int] = {}

    async def _delay_call(self, call_name: str) -> None:
        """Wait for a configured per-operation delay, when present."""
        delay = self.call_delays.get(call_name, 0.0)
        if delay > 0:
            await asyncio.sleep(delay)

    async def alloc(
        self,
        ctx: RequestContext,
        node_id: str,
        object_id: str,
        owner_identity: str,
        size: int,
    ) -> None:
        """Reserve node capacity or raise the next scripted failure."""
        self.call_log.append(
            ("alloc", (ctx, node_id, object_id, owner_identity, size))
        )
        await self._delay_call("alloc")
        if self.fail_next_alloc:
            raise self.fail_next_alloc.pop(0)
        # reject at alloc time if the node cannot fit it, nothing waits until first write
        if self.used + size > self.capacity:
            raise OutOfCapacityError(f"node {node_id} is full")
        self.reserved[object_id] = size
        self.owners[object_id] = owner_identity
        self.data[object_id] = b""       # object exists but is empty
        self.used += size

    async def write(
        self,
        ctx: RequestContext,
        node_id: str,
        object_id: str,
        owner_identity: str,
        data: bytes,
    ) -> None:
        """Replace the stored value for an allocated object."""
        self.call_log.append(
            ("write", (ctx, node_id, object_id, owner_identity, data))
        )
        self.active_write_calls += 1
        self.max_concurrent_write_calls = max(
            self.max_concurrent_write_calls,
            self.active_write_calls,
        )
        active_for_object = self.active_write_calls_by_object.get(object_id, 0) + 1
        self.active_write_calls_by_object[object_id] = active_for_object
        self.max_concurrent_write_calls_by_object[object_id] = max(
            self.max_concurrent_write_calls_by_object.get(object_id, 0),
            active_for_object,
        )
        try:
            await self._delay_call("write")
            if self.fail_next_write:
                raise self.fail_next_write.pop(0)
            # write does NOT create objects, only alloc does
            if object_id not in self.data:
                raise ObjectNotFoundError(object_id)
            # data must fit in the space reserved at alloc
            if len(data) > self.reserved[object_id]:
                raise InvalidRequestError("data larger than allocated size")
            self.data[object_id] = bytes(data)  # full replace, no partial writes
        finally:
            self.active_write_calls -= 1
            remaining_for_object = self.active_write_calls_by_object[object_id] - 1
            if remaining_for_object:
                self.active_write_calls_by_object[object_id] = remaining_for_object
            else:
                del self.active_write_calls_by_object[object_id]

    async def read(
        self,
        ctx: RequestContext,
        node_id: str,
        object_id: str,
        owner_identity: str,
    ) -> bytes:
        """Return the stored value for an allocated object."""
        self.call_log.append(
            ("read", (ctx, node_id, object_id, owner_identity))
        )
        await self._delay_call("read")
        if self.fail_next_read:
            raise self.fail_next_read.pop(0)
        # never allocated or already freed
        if object_id not in self.data:
            raise ObjectNotFoundError(object_id)
        return self.data[object_id]

    async def free(
        self,
        ctx: RequestContext,
        node_id: str,
        object_id: str,
        owner_identity: str,
    ) -> None:
        """Release a reservation or raise the next scripted failure."""
        self.call_log.append(
            ("free", (ctx, node_id, object_id, owner_identity))
        )
        await self._delay_call("free")
        if self.fail_next_free:
            raise self.fail_next_free.pop(0)
        # free is idempotent, unknown or already freed id is a silent no-op
        if object_id not in self.data:
            return
        self.used -= self.reserved.pop(object_id)   # give the space back
        del self.data[object_id]
        del self.owners[object_id]


class FakeScheduler:
    """Deterministic scheduler fake that consumes configured node choices."""

    def __init__(
        self,
        node_id: str = "node-1",
        node_ids: list[str] | None = None,
    ) -> None:
        self.node_id = node_id
        self.node_ids = list(node_ids) if node_ids is not None else [node_id]
        self.calls: list[dict[str, str | int]] = []

    def select_node(
        self,
        object_id: str,
        object_size: int,
        nodes: list[object],
    ) -> PlacementDecision:
        """Return the legacy fixed-node placement decision."""
        return PlacementDecision(object_id=object_id, node_id=self.node_id)

    def select_node_from_state(
        self,
        object_id: str,
        object_size: int,
    ) -> PlacementDecision:
        """Return and consume the next configured placement target."""
        self.calls.append(
            {"object_id": object_id, "object_size": object_size}
        )
        if not self.node_ids:
            raise RuntimeError("No eligible node available for placement.")

        return PlacementDecision(
            object_id=object_id,
            node_id=self.node_ids.pop(0),
        )


class FakeSecurity:
    def __init__(
        self,
        token_identities: dict[str, str] | None = None,
        trust_states: dict[str, TrustState] | None = None,
    ) -> None:
        self.token_identities = token_identities or {}
        self.trust_states = trust_states or {}
        self.events: list[SecurityEvent] = []
        self.access_checks: list[tuple[str, str, str, str]] = []
        self.revocation_listeners: list[Callable[[str, float], list[str]]] = []

    def check_access(
        self,
        identity: str,
        owner_identity: str,
        action: str,
        object_id: str,
    ) -> bool:
        """Allow only the owner and record each authorization decision."""
        self.access_checks.append(
            (identity, owner_identity, action, object_id)
        )
        return identity == owner_identity

    def register_revocation_listener(
        self,
        callback: Callable[[str, float], list[str]],
    ) -> None:
        """Store a revocation callback registered by the runtime."""
        self.revocation_listeners.append(callback)

    def revoke_node(self, node_id: str, timestamp: float = 0.0) -> list[list[str]]:
        """Fire registered callbacks for one revoked node."""
        return [
            callback(node_id, timestamp)
            for callback in self.revocation_listeners
        ]

    def get_trust_state(self, node_id: str) -> TrustState:
        return self.trust_states.get(node_id, TrustState.TRUSTED)

    def log_security_event(
        self,
        event_type: EventType,
        actor_id: str,
        severity: Severity,
        result: Result,
        request_id: str,
        **extra,
    ) -> SecurityEvent:
        event = SecurityEvent.create(
            event_type=event_type,
            actor_id=actor_id,
            severity=severity,
            result=result,
            request_id=request_id,
            **extra,
        )
        self.events.append(event)
        return event

    def verify_token(self, token: str) -> str:
        """Return the configured identity for a token."""
        try:
            return self.token_identities[token]
        except KeyError as exc:
            raise TokenInvalidError("unknown token") from exc