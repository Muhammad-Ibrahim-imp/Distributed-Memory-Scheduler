# Purpose:
# Aggregates the latest resource, network, and security state
# into NodeStats objects consumed by DefaultScheduler.
#
# Providers:
#   A1 -> ResourceStateProvider
#   B1 -> NetworkStateProvider
#   M5 -> SecurityStateProvider

from common.interfaces.network_state import (
    NetworkStateProvider,
)
from common.interfaces.node_state import (
    NodeStateProvider,
)
from common.interfaces.resource_state import (
    ResourceStateProvider,
)
from common.interfaces.security_state import (
    SecurityStateProvider,
)
from common.types.node import NodeStats


class NodeStatsAggregator(NodeStateProvider):

    def __init__(
        self,
        node_ids: list[str],
        resource_provider: ResourceStateProvider,
        network_provider: NetworkStateProvider,
        security_provider: SecurityStateProvider,
    ):
        self.node_ids = list(node_ids)
        self.resource_provider = resource_provider
        self.network_provider = network_provider
        self.security_provider = security_provider

    def get_nodes(self) -> list[NodeStats]:

        return [
            self.get_node_stats(node_id)
            for node_id in self.node_ids
        ]

    def get_node_stats(
        self,
        node_id: str,
    ) -> NodeStats:

        resource = (
            self.resource_provider
            .get_resource_state(node_id)
        )

        network = (
            self.network_provider
            .get_network_state(node_id)
        )

        trust_state = (
            self.security_provider
            .get_trust_state(node_id)
        )

        return NodeStats(
            node_id=node_id,
            free_ram=resource.free_ram,
            cpu_usage=resource.cpu_usage,
            latency=network.latency,
            bandwidth=network.bandwidth,
            health=resource.health,
            security_state=trust_state,
        )
