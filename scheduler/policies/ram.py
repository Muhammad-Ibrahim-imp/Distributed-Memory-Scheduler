# Purpose:
# Implements free-RAM-aware placement.
# Selects the node with the most available RAM.

from common.types.node import NodeStats
from scheduler.policies.base import PlacementPolicy


class RAMAwarePolicy(PlacementPolicy):

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
            key=lambda node: node.free_ram,
        )
