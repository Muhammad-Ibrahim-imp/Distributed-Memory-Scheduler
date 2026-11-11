from dataclasses import dataclass
from pathlib import Path

import psutil

from common.types.resource import ResourceStats
from memory_node.allocator.allocator import Allocator

DEFAULT_CGROUP_ROOT = Path("/sys/fs/cgroup")
_UNLIMITED = 1 << 60   # cgroup v1 reports a huge number when there is no limit


def read_container_memory(cgroup_root: Path = DEFAULT_CGROUP_ROOT) -> tuple[int, int] | None:
    """(limit_bytes, usage_bytes) if this process runs under a cgroup memory limit,
    otherwise None. Tries cgroup v2, then v1. Any read problem (Windows, macOS,
    no limit) gives None."""
    candidates = (
        (cgroup_root / "memory.max", cgroup_root / "memory.current"),                 # v2
        (cgroup_root / "memory" / "memory.limit_in_bytes",
         cgroup_root / "memory" / "memory.usage_in_bytes"),                           # v1
    )
    for limit_file, usage_file in candidates:
        try:
            raw = limit_file.read_text().strip()
            if raw == "max":
                return None
            limit = int(raw)
            usage = int(usage_file.read_text().strip())
        except (OSError, ValueError):
            continue
        if limit >= _UNLIMITED:
            return None
        return limit, usage
    return None
def effective_memory(cgroup_root: Path = DEFAULT_CGROUP_ROOT) -> tuple[int, int]:
    """(total, available) this process can really use: the container's limit when
    there is one below host RAM, otherwise host RAM (B2)."""
    vm = psutil.virtual_memory()
    container = read_container_memory(cgroup_root)
    if container is not None:
        limit, usage = container
        if limit < vm.total:
            return limit, max(0, limit - usage)
    return vm.total, vm.available


DEFAULT_HEADROOM_FRACTION = 0.10
DEFAULT_HEADROOM_MIN_BYTES = 128 * 1024 * 1024    # 128 MiB


@dataclass(frozen=True)
class PoolPlan:
    configured: int       # NODE_CAPACITY_BYTES as given
    effective_limit: int  # the memory this process can use in total
    headroom: int         # bytes kept back for Python, copies and the OS
    pool: int             # what the node actually offers

    @property
    def clamped(self) -> bool:
        return self.pool < self.configured


def plan_pool_capacity(configured: int, effective_limit: int,
                       headroom_fraction: float = DEFAULT_HEADROOM_FRACTION,
                       headroom_min_bytes: int = DEFAULT_HEADROOM_MIN_BYTES) -> PoolPlan:
    """pool = min(configured, effective_limit - headroom), where headroom is
    max(headroom_min_bytes, headroom_fraction * effective_limit) (B2).
    Raises ValueError if nothing usable is left, so startup is refused."""
    if configured <= 0:
        raise ValueError("configured capacity must be positive")
    if effective_limit <= 0:
        raise ValueError("effective memory limit must be positive")
    if not 0 <= headroom_fraction < 1:
        raise ValueError("headroom_fraction must be in [0, 1)")
    if headroom_min_bytes < 0:
        raise ValueError("headroom_min_bytes must not be negative")
    headroom = max(headroom_min_bytes, int(effective_limit * headroom_fraction))
    usable = effective_limit - headroom
    if usable <= 0:
        raise ValueError(
            f"no usable pool capacity: an effective limit of {effective_limit} bytes "
            f"leaves nothing after {headroom} bytes of headroom")
    return PoolPlan(configured, effective_limit, headroom, min(configured, usable))


@dataclass
class CapacityStats:
    """Physical HOST RAM, for the capacity experiment only (B2). Not part of B2's
    ResourceStateProvider and never called during scheduling. The experiment uses
    separate physical machines. Docker runs are container measurements (B2)."""
    total_ram: int
    used_ram: int
    free_ram: int


@dataclass
class NodeReport:
    """The heartbeat record B1 turns into NodeStatsReport (B1 chose option B).
    The memory figures describe the contributed POOL, not the host."""
    node_id: str
    total_memory: int       # bytes: pool capacity (total_capacity_bytes)
    used_memory: int        # bytes reserved by alloc
    available_memory: int   # bytes still allocatable
    cpu_percent: float      # 0 to 100
    object_count: int
    health: float           # 0.0 to 1.0, higher is healthier


class NodeResourceMonitor:    # if a shared ResourceStateProvider exists, subclass it
    def __init__(self, node_id: str, allocator: Allocator,
                 cgroup_root: Path = DEFAULT_CGROUP_ROOT):
        self.node_id = node_id
        self.allocator = allocator
        self._cgroup_root = cgroup_root
        psutil.cpu_percent(interval=None)   # warm-up: the first reading is meaningless

    def get_resource_state(self, node_id: str) -> ResourceStats:
        """For B2. free_ram is what can still be allocated from the node's contributed
        pool, not host RAM (confirmed by B2)."""
        self._check_node_id(node_id)
        cpu = psutil.cpu_percent(interval=None)
        return ResourceStats(free_ram=self.allocator.available(),
                             cpu_usage=cpu, health=self._health(cpu))

    def get_capacity_stats(self, node_id: str) -> CapacityStats:
        """Physical host RAM for the capacity experiment (B2)."""
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

    def effective_memory(self) -> tuple[int, int]:
        return effective_memory(self._cgroup_root)

    def _check_node_id(self, node_id: str) -> None:
        if node_id != self.node_id:
            raise ValueError(f"Reports only for '{self.node_id}', got '{node_id}'")

    def _health(self, cpu_usage: float) -> float:
        """[Recommendation] The host's condition: the worse of CPU load and memory
        pressure, using the container limit when there is one. Pool fullness is left
        out on purpose, because free_ram already reports it and B2's scorer treats
        them as separate inputs (confirmed)."""
        cpu_component = max(0.0, 1.0 - cpu_usage / 100)
        total, available = self.effective_memory()
        ram_component = min(1.0, (available / total) / 0.10) if total else 0.0
        return round(min(cpu_component, ram_component), 2)