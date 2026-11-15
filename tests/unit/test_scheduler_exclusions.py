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
