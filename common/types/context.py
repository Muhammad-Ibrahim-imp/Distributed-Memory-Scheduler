from dataclasses import dataclass

@dataclass(frozen=True)
class RequestContext:
    identity: str
    request_id: str
    coordinator_generation: str = ""