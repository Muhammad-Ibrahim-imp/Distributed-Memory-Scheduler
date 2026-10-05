from abc import ABC, abstractmethod
from dataclasses import dataclass
import psutil

from common.types.node import NodeStats   # shared type, do not redefine locally


@dataclass
class ResourceStats:
    free_ram: int
    cpu_usage: float
    health: float
    # [Open — flagged by M5] B2's report says total/used RAM are missing.
    # Pending reply from B2: either add fields here, or a separate
    # get_capacity_stats() method (message already sent).


class ResourceStateProvider(ABC):
    @abstractmethod
    def get_resource_state(self, node_id: str) -> ResourceStats:
        ...


class NodeResourceMonitor(ResourceStateProvider):
    def __init__(self, node_id: str, memory_limit_bytes: int | None = None):
        self.node_id = node_id
        self.memory_limit_bytes = memory_limit_bytes
        psutil.cpu_percent(interval=None)  # warm-up call

    def get_resource_state(self, node_id: str) -> ResourceStats:
        if node_id != self.node_id:
            raise ValueError(f"Reports only for '{self.node_id}', got '{node_id}'")
        free_ram = self._current_free_ram()
        cpu_usage = psutil.cpu_percent(interval=None)
        health = self._compute_health(free_ram, cpu_usage)
        return ResourceStats(free_ram=free_ram, cpu_usage=cpu_usage, health=health)

    def get_node_stats(self, object_count: int) -> NodeStats:
        """Returns B2's shared NodeStats type. B1 serializes it into
        NodeStatsReport for the wire — not your job."""
        vm_total = self.memory_limit_bytes or psutil.virtual_memory().total
        free = self._current_free_ram()
        resource = self.get_resource_state(self.node_id)
        return NodeStats(
            node_id=self.node_id,
            total_memory=vm_total,
            used_memory=vm_total - free,
            available_memory=free,
            cpu_percent=resource.cpu_usage,
            object_count=object_count,
            health=resource.health,
        )

    def _current_free_ram(self) -> int:
        vm = psutil.virtual_memory()
        if self.memory_limit_bytes is None:
            return vm.available
        raise NotImplementedError("Needs Allocator.used once allocator.py is implemented.")

    def _compute_health(self, free_ram: int, cpu_usage: float) -> float:
        """[Recommendation, not confirmed] simple heuristic."""
        cpu_component = max(0.0, 1.0 - (cpu_usage / 100))
        vm = psutil.virtual_memory()
        ram_ratio = free_ram / vm.total if vm.total else 0.0
        ram_component = min(1.0, ram_ratio / 0.10)
        return round(min(cpu_component, ram_component), 2)