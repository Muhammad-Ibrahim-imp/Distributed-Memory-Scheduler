import time
from dataclasses import dataclass

CAPACITY = 20
REFILL_PER_SECOND = 5.0


@dataclass
class _Bucket:
    tokens: float
    last: float


class RateLimiter:
    def __init__(self, capacity: int = CAPACITY,
                 refill_per_second: float = REFILL_PER_SECOND,
                 clock=time.time):
        self.capacity = capacity
        self.refill = refill_per_second
        self.clock = clock
        self._buckets: dict[str, _Bucket] = {}

    def check(self, identity: str) -> bool:
        now = self.clock()
        b = self._buckets.get(identity)
        if b is None:
            self._buckets[identity] = _Bucket(tokens=self.capacity - 1, last=now)
            return True
        elapsed = now - b.last
        b.tokens = min(self.capacity, b.tokens + elapsed * self.refill)
        b.last = now
        if b.tokens < 1.0:
            return False
        b.tokens -= 1.0
        return True