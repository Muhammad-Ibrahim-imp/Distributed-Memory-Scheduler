class Allocator:
    """Tracks and reserves local RAM for this memory node."""

    def __init__(self, total_capacity_bytes: int):
        self.total_capacity = total_capacity_bytes
        self.used = 0

    def reserve(self, size: int) -> bool:
        raise NotImplementedError

    def release(self, size: int) -> None:
        raise NotImplementedError

    def available(self) -> int:
        return self.total_capacity - self.used