# Purpose:
# Implements latency-aware placement.
# Selects the node with the lowest measured network latency.

from common.types.node import NodeStats
from scheduler.policies.base import PlacementPolicy


class LatencyAwarePolicy(PlacementPolicy):

    def select(
        self,
        nodes: list[NodeStats],
    ) -> NodeStats:

        if not nodes:
            raise ValueError(
                "Cannot select from an empty node list."
            )

        return min(
            nodes,
            key=lambda node: node.latency,
        )
