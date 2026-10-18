import time
from dataclasses import dataclass

WINDOW_SECONDS = 60
SKEW_SECONDS = 5


@dataclass
class ReplayResult:
    ok: bool
    reason: str = ""


class ReplayGuard:
    def __init__(self, window_seconds: int = WINDOW_SECONDS,
                 skew_seconds: int = SKEW_SECONDS,
                 clock=time.time):
        self.window = window_seconds
        self.skew = skew_seconds
        self.clock = clock
        self._seen: dict[tuple[str, str], float] = {}

    def check(self, identity: str, nonce: str, timestamp: float) -> ReplayResult:
        now = self.clock()
        # Sweep FIRST so an entry that has aged out is not falsely flagged.
        self._sweep(now)
        if abs(now - timestamp) > self.window + self.skew:
            return ReplayResult(False, "timestamp_out_of_window")
        key = (identity, nonce)
        if key in self._seen:
            return ReplayResult(False, "replay_detected")
        self._seen[key] = now
        return ReplayResult(True)

    def _sweep(self, now: float) -> None:
        cutoff = now - (self.window + self.skew)
        for key, t in list(self._seen.items()):
            if t < cutoff:
                del self._seen[key]