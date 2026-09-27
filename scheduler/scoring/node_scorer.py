from common.types.node import NodeStats
from common.types.scheduler import SchedulerConfig

class NodeScorer:
    def __init__(self, config: SchedulerConfig):
        self.config = config
    def score_node(self, node: NodeStats, nodes: List[NodeStats]) -> float:
        ram_score = high_is_better(
            node.free_ram,
            [n.free_ram for n in nodes] )
        cpu_score = low_is_better(
            node.free_cpu,
            [n.free_cpu for n in nodes] )
        latency_score = low_is_better(
            node.latency,
            [n.latency for n in nodes]
        )
        bandwidth_score = high_is_better(
            node.bandwidth,
            [n.bandwidth for n in nodes]
        )
        health_score = high_is_better(
            node.health,
            [n.health for n in nodes]
        )
        return (ram_score * self.config.ram_weight +
                cpu_score * self.config.cpu_weight +
                latency_score * self.config.latency_weight +
                bandwidth_score * self.config.bandwidth_weight +
                health_score * self.config.health_weight)

    @staticmethod
    def low_is_better(value: float, values: List[float]) -> float:
        maximum = max(values)
        minimum = min(values)
        if minimum == maximum:
            return 1
        return (maximum - value) / (maximum - minimum)
    @staticmethod
    def high_is_better(value: float, values: List[float]) -> float:
        maximum = max(values)
        minimum = min(values)
        if minimum == maximum:
            return 1
        return (value - minimum) / (maximum - minimum)
