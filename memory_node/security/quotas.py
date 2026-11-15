from common.types.errors import QuotaExceededError


class QuotaTracker:
    """Per-client limits on allocated bytes and object count. None means unlimited.
    Memory usage counts the SIZE reserved at alloc, so writes (which can't exceed
    that size) never change it."""

    def __init__(self, max_memory_per_client: int | None = None,
                 max_objects_per_client: int | None = None):
        self.max_memory_per_client = max_memory_per_client
        self.max_objects_per_client = max_objects_per_client
        self._usage: dict[str, dict[str, int]] = {}

    @classmethod
    def from_fraction(cls, total_capacity_bytes: int,
                      max_memory_fraction_per_client: float,
                      max_objects_per_client: int | None = None) -> "QuotaTracker":
        """Matches M5's config: max_memory_fraction_per_client * node capacity."""
        if total_capacity_bytes <= 0:
            raise ValueError("total_capacity_bytes must be positive")
        if not 0 < max_memory_fraction_per_client <= 1:
            raise ValueError("fraction must be in (0, 1]")
        return cls(int(total_capacity_bytes * max_memory_fraction_per_client),
                   max_objects_per_client)

    def check_within_quota(self, owner: str, additional_bytes: int,
                           additional_objects: int = 1) -> None:
        if additional_bytes < 0 or additional_objects < 0:
            raise ValueError("additions must not be negative")
        used = self.usage(owner)
        if (self.max_memory_per_client is not None
                and used["bytes"] + additional_bytes > self.max_memory_per_client):
            raise QuotaExceededError("memory quota exceeded")
        if (self.max_objects_per_client is not None
                and used["objects"] + additional_objects > self.max_objects_per_client):
            raise QuotaExceededError("object quota exceeded")

    def record_alloc(self, owner: str, size: int, objects: int = 1) -> None:
        u = self._usage.setdefault(owner, {"bytes": 0, "objects": 0})
        u["bytes"] += size
        u["objects"] += objects

    def record_free(self, owner: str, size: int, objects: int = 1) -> None:
        u = self._usage.get(owner)
        if u is None or size > u["bytes"] or objects > u["objects"]:
            raise ValueError("freeing more than was recorded")
        u["bytes"] -= size
        u["objects"] -= objects
        if u["bytes"] == 0 and u["objects"] == 0:
            del self._usage[owner]

    def usage(self, owner: str) -> dict[str, int]:
        u = self._usage.get(owner)
        return dict(u) if u else {"bytes": 0, "objects": 0}