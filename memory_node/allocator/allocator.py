class Allocator:
    """Tracks how much of this node's RAM budget is reserved. Counts bytes only;
    ObjectStore holds the data. No awaits here, so under asyncio a call can't be
    interrupted halfway by another request."""

    def __init__(self, total_capacity_bytes: int):
        if total_capacity_bytes <= 0:
            raise ValueError("total_capacity_bytes must be positive")
        self.total_capacity = total_capacity_bytes
        self.used = 0

    def reserve(self, size: int) -> bool:
        """False (and no change) if there is not enough room.
        node.py turns False into OutOfCapacityError."""
        if size < 0:
            raise ValueError("size must not be negative")
        if size > self.available():
            return False
        self.used += size
        return True

    def release(self, size: int) -> None:
        if size < 0:
            raise ValueError("size must not be negative")
        if size > self.used:
            # Releasing more than reserved means an accounting bug elsewhere.
            # Failing loudly beats hiding it by clamping at zero.
            raise ValueError("cannot release more than is reserved")
        self.used -= size

    def available(self) -> int:
        return self.total_capacity - self.used