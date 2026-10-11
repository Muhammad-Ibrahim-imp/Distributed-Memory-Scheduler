# Purpose:
# Implements balanced resource-aware scheduling using NodeScorer.

from common.types.node import NodeStats
from common.types.scheduler import SchedulerConfig

from scheduler.policies.base import PlacementPolicy
from scheduler.scoring.node_scorer import NodeScorer


class BalancedPolicy(PlacementPolicy):

    def __init__(
        self,
        config: SchedulerConfig,
    ):
        self.scorer = NodeScorer(config)

    def score(
        self,
        node: NodeStats,
        nodes: list[NodeStats],
    ) -> float:

        return self.scorer.score(
            node,
            nodes,
        )

    def select(
        self,
        nodes: list[NodeStats],
    ) -> NodeStats:

        if not nodes:
            raise ValueError(
                "Cannot select from an empty node list."
            )

        return max(
            nodes,
            key=lambda node: self.score(
                node,
                nodes,
            ),
        )
