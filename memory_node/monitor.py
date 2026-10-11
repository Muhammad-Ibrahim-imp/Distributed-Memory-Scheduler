from abc import ABC, abstractmethod
from dataclasses import dataclass
import psutil

from common.types.node import NodeStats   # shared type, do not redefine


@dataclass
class ResourceStats:
    # If this class already exists in common/types/, import it instead.
    free_ram: int       # bytes
    cpu_usage: float    # 0-100 %
    health: float       # 0.0-1.0


@dataclass
class CapacityStats:
    """For the performance experiment. B2 does not own or require this."""
    total_ram: int
    used_ram: int
    free_ram: int


class ResourceStateProvider(ABC):
    @abstractmethod
    def get_resource_state(self, node_id: str) -> ResourceStats:
        raise NotImplementedError

    @abstractmethod
    def get_capacity_stats(self, node_id: str) -> CapacityStats:
        raise NotImplementedError


class NodeResourceMonitor(ResourceStateProvider):
    def __init__(self, node_id: str, memory_limit_bytes: int | None = None):
        self.node_id = node_id
        self.memory_limit_bytes = memory_limit_bytes
        psutil.cpu_percent(interval=None)   # warm-up: first call is meaningless

    def get_resource_state(self, node_id: str) -> ResourceStats:
        self._check_node_id(node_id)
        free_ram = self._current_free_ram()
        cpu_usage = psutil.cpu_percent(interval=None)
        return ResourceStats(free_ram=free_ram, cpu_usage=cpu_usage,
                             health=self._compute_health(free_ram, cpu_usage))

    def get_capacity_stats(self, node_id: str) -> CapacityStats:
        self._check_node_id(node_id)
        total = self.memory_limit_bytes or psutil.virtual_memory().total
        free = self._current_free_ram()
        return CapacityStats(total_ram=total, used_ram=total - free, free_ram=free)

    def get_node_stats(self, object_count: int) -> NodeStats:
        """Verify NodeStats' real field names after pulling main."""
        cap = self.get_capacity_stats(self.node_id)
        res = self.get_resource_state(self.node_id)
        return NodeStats(
            node_id=self.node_id, total_memory=cap.total_ram,
            used_memory=cap.used_ram, available_memory=cap.free_ram,
            cpu_percent=res.cpu_usage, object_count=object_count,
            health=res.health,
        )

    def _check_node_id(self, node_id: str) -> None:
        if node_id != self.node_id:
            raise ValueError(f"Reports only for '{self.node_id}', got '{node_id}'")

    def _current_free_ram(self) -> int:
        if self.memory_limit_bytes is None:
            return psutil.virtual_memory().available
        raise NotImplementedError("Needs Allocator.used once allocator is implemented.")

    def _compute_health(self, free_ram: int, cpu_usage: float) -> float:
        """Recommendation, not confirmed: the worse of CPU load and free RAM."""
        cpu_component = max(0.0, 1.0 - cpu_usage / 100)
        total = psutil.virtual_memory().total
        ram_component = min(1.0, (free_ram / total) / 0.10) if total else 0.0
        return round(min(cpu_component, ram_component), 2)