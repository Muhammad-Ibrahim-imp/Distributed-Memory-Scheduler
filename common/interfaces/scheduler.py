# Purpose:
# Defines the scheduler API consumed by the DSM layer/A2.

from abc import ABC, abstractmethod

from common.types.node import NodeStats
from common.types.placement import PlacementDecision


class Scheduler(ABC):

    @abstractmethod
    def select_node(
        self,
        object_id: str,
        object_size: int,
        nodes: list[NodeStats],
    ) -> PlacementDecision:
        """Select a node for storing a DSM object."""
        raise NotImplementedError
