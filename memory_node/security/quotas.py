from common.types.errors import QuotaExceededError


class QuotaTracker:
    def __init__(self, max_memory_per_client: int | None = None,
                 max_objects_per_client: int | None = None):
        self._usage: dict[str, dict] = {}
        self.max_memory_per_client = max_memory_per_client    # [Open] values, Week 2
        self.max_objects_per_client = max_objects_per_client  # [Open] values, Week 2

    def check_within_quota(self, owner: str, additional_bytes: int) -> None:
        raise NotImplementedError
        # usage = self._usage.get(owner, {"bytes": 0, "objects": 0})
        # if usage["bytes"] + additional_bytes > self.max_memory_per_client:
        #     raise QuotaExceededError(...)
        # if usage["objects"] + 1 > self.max_objects_per_client:
        #     raise QuotaExceededError(...)