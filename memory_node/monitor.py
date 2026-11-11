from dataclasses import dataclass

import psutil

from common.types.resource import ResourceStats
from memory_node.allocator.allocator import Allocator


@dataclass
class CapacityStats:
    """Physical HOST RAM, for the capacity experiment only (B2). It is not part of
    B2's ResourceStateProvider and is never called during scheduling."""
    total_ram: int
    used_ram: int
    free_ram: int


@dataclass
class NodeReport:
    """The heartbeat record B1 turns into NodeStatsReport (B1 chose option B).
    The memory figures describe the contributed POOL, not the host.
    [ASSUMED] pool meaning and this type's location, pending B1."""
    node_id: str
    total_memory: int       # bytes: pool capacity (total_capacity_bytes)
    used_memory: int        # bytes reserved by alloc
    available_memory: int   # bytes still allocatable
    cpu_percent: float      # 0 to 100
    object_count: int
    health: float           # 0.0 to 1.0


class NodeResourceMonitor:    # if a shared ResourceStateProvider exists, subclass it
    def __init__(self, node_id: str, allocator: Allocator):
        self.node_id = node_id
        self.allocator = allocator
        psutil.cpu_percent(interval=None)   # warm-up: the first reading is meaningless

    def get_resource_state(self, node_id: str) -> ResourceStats:
        """For B2. free_ram is what can still be allocated from the node's contributed
        pool, not host RAM (confirmed by B2)."""
        self._check_node_id(node_id)
        cpu = psutil.cpu_percent(interval=None)
        return ResourceStats(free_ram=self.allocator.available(),
                             cpu_usage=cpu, health=self._health(cpu))

    def get_capacity_stats(self, node_id: str) -> CapacityStats:
        """Host RAM for the capacity experiment (B2). Inside Docker, psutil reports
        the host's memory, not the container's limit."""
        self._check_node_id(node_id)
        vm = psutil.virtual_memory()
        return CapacityStats(total_ram=vm.total,
                             used_ram=vm.total - vm.available,
                             free_ram=vm.available)

    def get_node_report(self, object_count: int) -> NodeReport:
        cpu = psutil.cpu_percent(interval=None)
        return NodeReport(
            node_id=self.node_id,
            total_memory=self.allocator.total_capacity,
            used_memory=self.allocator.used,
            available_memory=self.allocator.available(),
            cpu_percent=cpu,
            object_count=object_count,
            health=self._health(cpu),
        )

    def _check_node_id(self, node_id: str) -> None:
        if node_id != self.node_id:
            raise ValueError(f"Reports only for '{self.node_id}', got '{node_id}'")

    def _health(self, cpu_usage: float) -> float:
        """[Recommendation, not confirmed] The host's condition: the worse of CPU load
        and host RAM pressure. Pool fullness is deliberately left out, because
        free_ram already reports it and counting it twice would penalise a busy node
        twice."""
        cpu_component = max(0.0, 1.0 - cpu_usage / 100)
        vm = psutil.virtual_memory()
        ram_component = min(1.0, (vm.available / vm.total) / 0.10) if vm.total else 0.0
        return round(min(cpu_component, ram_component), 2)