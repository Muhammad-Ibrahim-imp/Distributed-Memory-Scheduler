import pytest

from common.types.node import NodeStats
from common.types.security import TrustState
from scheduler.scheduler import DefaultScheduler


class FirstNodePolicy:
    def select(self, nodes: list[NodeStats]) -> NodeStats:
        return nodes[0]


class StaticNodeState:
    def __init__(self, nodes: list[NodeStats]) -> None:
        self.nodes = nodes

    def get_nodes(self) -> list[NodeStats]:
        return self.nodes


class MutableTrust:
    def __init__(self, state: TrustState) -> None:
        self.state = state
        self.reads = 0

    def get_trust_state(self, node_id: str) -> TrustState:
        self.reads += 1
        return self.state


def test_state_scheduler_excludes_nodes_that_already_rejected_alloc() -> None:
    scheduler = DefaultScheduler(
        policy=FirstNodePolicy(),
        node_state_provider=StaticNodeState(
            [
                NodeStats("node-1", 100, 0, 0, 0, 1, TrustState.TRUSTED),
                NodeStats("node-2", 100, 0, 0, 0, 1, TrustState.TRUSTED),
            ]
        ),
    )

    decision = scheduler.select_node_from_state(
        object_id="object-1",
        object_size=10,
        excluded_node_ids={"node-1"},
    )

    assert decision.node_id == "node-2"


@pytest.mark.parametrize("state", [TrustState.SUSPICIOUS, TrustState.QUARANTINED, TrustState.REVOKED])
def test_scheduler_refreshes_and_excludes_every_non_trusted_state(state: TrustState) -> None:
    trust = MutableTrust(state)
    scheduler = DefaultScheduler(policy=FirstNodePolicy(), security_provider=trust)

    with pytest.raises(RuntimeError, match="No eligible node"):
        scheduler.select_node(
            object_id="object-1",
            object_size=1,
            nodes=[NodeStats("node-1", 100, 0, 0, 0, 1, TrustState.TRUSTED)],
        )

    assert trust.reads == 1
