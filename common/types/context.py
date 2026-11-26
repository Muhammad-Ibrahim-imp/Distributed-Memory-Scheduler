from dataclasses import dataclass

@dataclass(frozen=True)
class RequestContext:
    identity: str
    request_id: str
    # None is reserved for trusted in-process callers; network boundaries must
    # supply and validate an explicit active generation.
    coordinator_generation: str | None = None
