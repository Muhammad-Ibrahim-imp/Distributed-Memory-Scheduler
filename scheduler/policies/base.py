# Purpose:
# Defines the common interface for placement policies.

from abc import ABC, abstractmethod

from common.types.node import NodeStats


class PlacementPolicy(ABC):

    @abstractmethod
    def select(
        self,
        nodes: list[NodeStats],
    ) -> NodeStats:
        """Select one node from eligible nodes."""
        raise NotImplementedError
