# Purpose:
# Defines the resource information contract supplied to B2 by A1.
# B2 does not measure these values itself.

from dataclasses import dataclass


@dataclass
class ResourceStats:
    free_ram: int
    cpu_usage: float
    health: float

    def __post_init__(self):
        if self.free_ram < 0:
            raise ValueError("free_ram cannot be negative.")

        if not 0.0 <= self.cpu_usage <= 100.0:
            raise ValueError(
                "cpu_usage must be between 0 and 100."
            )

        if not 0.0 <= self.health <= 1.0:
            raise ValueError(
                "health must be between 0 and 1."
            )
