# Purpose:
# Defines A1 -> B2 resource-state integration.

from abc import ABC, abstractmethod

from common.types.resource import ResourceStats


class ResourceStateProvider(ABC):

    @abstractmethod
    def get_resource_state(
        self,
        node_id: str,
    ) -> ResourceStats:
        """Return current resource state for a node."""
        raise NotImplementedError
