# Purpose:
# Represents B2's final placement decision returned to A2.

from dataclasses import dataclass


@dataclass
class PlacementDecision:
    object_id: str
    node_id: str
    score: float | None = None
