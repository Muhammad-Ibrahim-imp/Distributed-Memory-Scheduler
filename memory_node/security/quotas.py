class QuotaTracker:
    """[Open — blocking] QUOTA_EXCEEDED has no exception class yet.
    Waiting on M5: either QuotaExceededError gets added, or we fall
    back to raising OutOfCapacityError as a stand-in."""

    def __init__(self, max_memory_per_client: int | None = None, max_objects_per_client: int | None = None):
        self._usage: dict[str, dict] = {}
        self.max_memory_per_client = max_memory_per_client     # [Open] pending M5
        self.max_objects_per_client = max_objects_per_client   # [Open] pending M5

    def check_within_quota(self, owner: str, additional_bytes: int) -> None:
        raise NotImplementedError