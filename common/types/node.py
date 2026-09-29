# Purpose:
# Defines the complete node state consumed by the scheduler.
# NodeStats is assembled from A1, B1, and M5 information.

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
    access_frequency: float = 0.0

    def __post_init__(self):
        if not self.node_id:
            raise ValueError("node_id cannot be empty.")

        if self.free_ram < 0:
            raise ValueError(
                "free_ram cannot be negative."
            )

        if not 0.0 <= self.cpu_usage <= 100.0:
            raise ValueError(
                "cpu_usage must be between 0 and 100."
            )

        if self.latency < 0:
            raise ValueError(
                "latency cannot be negative."
            )

        if self.bandwidth < 0:
            raise ValueError(
                "bandwidth cannot be negative."
            )

        if not 0.0 <= self.health <= 1.0:
            raise ValueError(
                "health must be between 0 and 1."
            )

        if not 0.0 <= self.access_frequency <= 1.0:
            raise ValueError(
                "access_frequency must be between 0 and 1."
            )

        if not isinstance(
            self.security_state,
            SecurityState,
        ):
            raise TypeError(
                "security_state must be a SecurityState."
            )
