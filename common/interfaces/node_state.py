# Purpose:
# Defines the interface used by B2 to obtain current node state.

from abc import ABC, abstractmethod

from common.types.node import NodeStats


class NodeStateProvider(ABC):

    @abstractmethod
    def get_nodes(self) -> list[NodeStats]:
        """Return current node states."""
        raise NotImplementedError
