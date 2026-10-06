from dataclasses import dataclass

# shared error from M5's frozen file; if the name differs on main, fix this import
from common.types.errors import ObjectNotFoundError


class FakeMemoryNode:
    """Stand-in for A1's MemoryNodeAPI (async store/load/delete). Duck-typed so the
    real node can replace it in week 4 without touching runtime code."""

    def __init__(self) -> None:
        # object_id -> bytes
        self._data: dict[str, bytes] = {}

    async def store(self, object_id: str, owner: str, data: bytes) -> None:
        # full replace only, A1 doesn't support partial or range writes
        self._data[object_id] = data

    async def load(self, object_id: str, owner: str) -> bytes:
        # A1 raises not-found if it doesn't hold the object (directory can go stale)
        if object_id not in self._data:
            raise ObjectNotFoundError(object_id)
        return self._data[object_id]

    async def delete(self, object_id: str, owner: str) -> None:
        # A1 raises not-found here too; A2's free() must catch it and treat it as a no-op
        if object_id not in self._data:
            raise ObjectNotFoundError(object_id)
        del self._data[object_id]


@dataclass
class FakePlacementDecision:
    # same shape as B2's PlacementDecision
    object_id: str
    node_id: str
    score: float | None = None


class FakeScheduler:
    """Stand-in for B2. Same method name and args as the real (sync) call."""

    def select_node_from_state(self, object_id: str, object_size: int) -> FakePlacementDecision:
        # always the same node, enough until the real scheduler is wired in
        return FakePlacementDecision(object_id=object_id, node_id="node-1", score=1.0)


class FakeTrustReader:
    """Stand-in for M5's trust-state reader; only get_trust_state(node_id) is used."""

    def __init__(self) -> None:
        # node_id -> state; empty means everything is trusted
        self._states: dict[str, str] = {}

    def set_state(self, node_id: str, state: str) -> None:
        # test helper to simulate a node going QUARANTINED or REVOKED
        self._states[node_id] = state

    def get_trust_state(self, node_id: str) -> str:
        # synchronous dict lookup. "TRUSTED" is a placeholder until M5 sends the real enum values
        return self._states.get(node_id, "TRUSTED")