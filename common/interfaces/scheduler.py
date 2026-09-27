from abc import ABC, abstractmethod

from common.types.placement import PlacementDecision
from common.types.node import NodeStats

#Selects a node for storing a DSM object.
class SchedulerInterface(ABC):

    @abstractmethod
    def select_node(
            self,
            object_id: str,
            object_size: int,
            nodes: List[NodeStats]
    ) -> PlacementDecision:


        raise NotImplementedError