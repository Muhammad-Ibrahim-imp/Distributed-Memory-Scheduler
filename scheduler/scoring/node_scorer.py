# Purpose:
# Calculates the normalized weighted score used by the balanced scheduler.
#
# Higher is better:
#   free RAM
#   bandwidth
#   health
#
# Lower is better:
#   CPU utilization
#   latency
#   scheduler placement frequency

from common.types.node import NodeStats
from common.types.scheduler import SchedulerConfig


class NodeScorer:

    def __init__(
        self,
        config: SchedulerConfig,
    ):
        self.config = config

    def score(
        self,
        node: NodeStats,
        nodes: list[NodeStats],
    ) -> float:

        if not nodes:
            raise ValueError(
                "Cannot score against an empty node list."
            )

        if node not in nodes:
            raise ValueError(
                "Node being scored must exist in nodes."
            )

        ram_score = self._higher_is_better(
            node.free_ram,
            [n.free_ram for n in nodes],
        )

        cpu_score = self._lower_is_better(
            node.cpu_usage,
            [n.cpu_usage for n in nodes],
        )

        latency_score = self._lower_is_better(
            node.latency,
            [n.latency for n in nodes],
        )

        bandwidth_score = self._higher_is_better(
            node.bandwidth,
            [n.bandwidth for n in nodes],
        )

        health_score = node.health

        access_frequency_score = self._lower_is_better(
            node.access_frequency,
            [n.access_frequency for n in nodes],
        )

        weighted_score = (
            ram_score * self.config.ram_weight
            + cpu_score * self.config.cpu_weight
            + latency_score * self.config.latency_weight
            + bandwidth_score * self.config.bandwidth_weight
            + health_score * self.config.health_weight
            + access_frequency_score
            * self.config.access_frequency_weight
        )

        total_weight = (
            self.config.ram_weight
            + self.config.cpu_weight
            + self.config.latency_weight
            + self.config.bandwidth_weight
            + self.config.health_weight
            + self.config.access_frequency_weight
        )

        return weighted_score / total_weight

    @staticmethod
    def _higher_is_better(
        value: float,
        values: list[float],
    ) -> float:

        minimum = min(values)
        maximum = max(values)

        if maximum == minimum:
            return 1.0

        return (
            (value - minimum)
            / (maximum - minimum)
        )

    @staticmethod
    def _lower_is_better(
        value: float,
        values: list[float],
    ) -> float:

        minimum = min(values)
        maximum = max(values)

        if maximum == minimum:
            return 1.0

        return (
            (maximum - value)
            / (maximum - minimum)
        )
