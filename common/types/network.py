# Purpose:
# Defines the network information contract supplied to B2 by B1.
# B2 does not implement socket/network measurement.

from dataclasses import dataclass


@dataclass
class NetworkStats:
    latency: float
    bandwidth: float

    def __post_init__(self):
        if self.latency < 0:
            raise ValueError("latency cannot be negative.")

        if self.bandwidth < 0:
            raise ValueError("bandwidth cannot be negative.")
