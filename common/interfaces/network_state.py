# Purpose:
# Defines B1 -> B2 network-state integration.

from abc import ABC, abstractmethod

from common.types.network import NetworkStats


class NetworkStateProvider(ABC):

    @abstractmethod
    def get_network_state(
        self,
        node_id: str,
    ) -> NetworkStats:
        """Return current network state for a node."""
        raise NotImplementedError
