"""Process-lifetime identifier for coordinator-owned DSM state."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4


@dataclass(frozen=True)
class CoordinatorGeneration:
    """Canonical UUID4 generation; create once before opening the listener."""

    value: str

    def __post_init__(self) -> None:
        parsed = UUID(self.value)
        if parsed.version != 4 or str(parsed) != self.value:
            raise ValueError("coordinator generation must be a canonical lowercase UUID4")

    @classmethod
    def create(cls) -> "CoordinatorGeneration":
        return cls(str(uuid4()))
