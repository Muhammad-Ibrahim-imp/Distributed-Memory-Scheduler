# Purpose:
# Stores scheduler configuration and placement-scoring weights.

from dataclasses import dataclass


@dataclass
class SchedulerConfig:
    policy: str = "balanced"

    ram_weight: float = 0.30
    cpu_weight: float = 0.20
    latency_weight: float = 0.15
    bandwidth_weight: float = 0.15
    health_weight: float = 0.10
    access_frequency_weight: float = 0.10

    def __post_init__(self):
        weights = (
            self.ram_weight,
            self.cpu_weight,
            self.latency_weight,
            self.bandwidth_weight,
            self.health_weight,
            self.access_frequency_weight,
        )

        if any(weight < 0 for weight in weights):
            raise ValueError(
                "Scheduler weights cannot be negative."
            )

        if sum(weights) <= 0:
            raise ValueError(
                "At least one scheduler weight must be greater than zero."
            )
