# Purpose:
# Implements bandwidth-aware placement.
# Selects the node with the highest available bandwidth.

from common.types.node import NodeStats
from scheduler.policies.base import PlacementPolicy


class BandwidthAwarePolicy(PlacementPolicy):

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
            key=lambda node: node.bandwidth,
        )
