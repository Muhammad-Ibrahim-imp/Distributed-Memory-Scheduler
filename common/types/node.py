from dataclasses import dataclass

from .security import SecurityState

@dataclass
class NodeStats:
    node_id: str
    free_ram: int
    cpu_usage: float
    latency: float
    bandwidth: float
    health: float
    security_state: SecurityState
