# Purpose:
# Provides B2's performance-scaling result model and calculates:
#
#   Speedup(N)    = T1 / TN
#   Efficiency(N) = Speedup(N) / N
#
# Resource/network metrics are supplied by their owners:
#   CPU utilization       -> A1
#   Memory utilization    -> A1
#   Network latency       -> B1
#   Network throughput    -> B1
#
# B2 does not use psutil or implement network measurement here.

from dataclasses import dataclass


@dataclass
class ScalingResult:
    node_count: int
    execution_time: float
    throughput: float

    cpu_utilization: float
    memory_utilization: float

    network_latency: float
    network_throughput: float

    workload_distribution: dict[str, int]

    speedup: float = 0.0
    efficiency: float = 0.0
    scheduler_overhead: float = 0.0
    network_overhead: float = 0.0

    def __post_init__(self):

        if self.node_count <= 0:
            raise ValueError(
                "node_count must be greater than zero."
            )

        if self.execution_time <= 0:
            raise ValueError(
                "execution_time must be greater than zero."
            )

        if self.throughput < 0:
            raise ValueError(
                "throughput cannot be negative."
            )

        if not 0.0 <= self.cpu_utilization <= 100.0:
            raise ValueError(
                "cpu_utilization must be between 0 and 100."
            )

        if not 0.0 <= self.memory_utilization <= 100.0:
            raise ValueError(
                "memory_utilization must be between 0 and 100."
            )

        if self.network_latency < 0:
            raise ValueError(
                "network_latency cannot be negative."
            )

        if self.network_throughput < 0:
            raise ValueError(
                "network_throughput cannot be negative."
            )

        if self.scheduler_overhead < 0:
            raise ValueError(
                "scheduler_overhead cannot be negative."
            )

        if self.network_overhead < 0:
            raise ValueError(
                "network_overhead cannot be negative."
            )


def calculate_scaling(
    results: list[ScalingResult],
) -> list[ScalingResult]:

    if not results:
        return []

    results = sorted(
        results,
        key=lambda result: result.node_count,
    )

    baseline = results[0]
    t1 = baseline.execution_time

    if t1 <= 0:
        raise ValueError(
            "Baseline execution time must be greater than zero."
        )

    for result in results:

        result.speedup = (
            t1 / result.execution_time
        )

        result.efficiency = (
            result.speedup / result.node_count
        )

    return results
