from common.interfaces.network_state import NetworkStateProvider
from common.types.network import NetworkStats
from network.tcp.registry import NodeRegistry


class RegistryNetworkStateProvider(NetworkStateProvider):
    """Reads cached latency (from heartbeat RTT) and bandwidth (from probe)."""

    def __init__(self, registry: NodeRegistry, default_bandwidth_mbps: float = 100.0):
        self.registry = registry
        self.default_bandwidth_mbps = default_bandwidth_mbps

    def get_network_state(self, node_id: str) -> NetworkStats:
        entry = self.registry.get(node_id)
        if entry is None:
            raise RuntimeError(f"no network state for node {node_id}")
        latency = entry.rtt_ms if entry.rtt_ms > 0 else 0.0
        bandwidth = getattr(entry, "bandwidth_mbps", self.default_bandwidth_mbps)
        return NetworkStats(latency=latency, bandwidth=bandwidth)