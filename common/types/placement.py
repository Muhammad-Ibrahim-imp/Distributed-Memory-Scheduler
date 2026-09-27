from dataclasses import dataclass

@dataclass
class PlacementDecision:
    object_id: str
    node_id: str
    score: float