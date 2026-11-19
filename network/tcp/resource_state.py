from common.interfaces.resource_state import ResourceStateProvider
from common.types.resource import ResourceStats
from network.tcp.registry import NodeRegistry


class RegistryResourceProvider(ResourceStateProvider):
    """Reads each node's latest heartbeat (NodeStatsReport) from the registry.

    free_ram comes from the node's contributed pool (available_memory), which is
    what A1 reports and what B2's scheduler compares against object_size.
    A node with no heartbeat yet raises, so it is skipped until its first report.
    """

    def __init__(self, registry: NodeRegistry):
        self.registry = registry

    def get_resource_state(self, node_id: str) -> ResourceStats:
        entry = self.registry.get(node_id)
        if entry is None or entry.last_stats is None:
            raise RuntimeError(f"no resource state for node {node_id}")
        s = entry.last_stats
        return ResourceStats(
            free_ram=int(s.available_memory),
            cpu_usage=min(100.0, max(0.0, float(s.cpu_percent))),
            health=min(1.0, max(0.0, float(s.health))),
        )