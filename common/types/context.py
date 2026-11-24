"""RequestContext — authenticated sender and request metadata scoped to a
coordinator generation.

Owned by B1 (used by the network layer and passed to every node call).
Imported by A1 (memory_node.node) and A2 (dsm runtime).

Frozen: the context of one request must not mutate mid-flight. If a field needs
to differ, build a new RequestContext.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    identity: str      # the verified sender identity (envelope.identity after AuthService.validate)
    request_id: str    # the envelope's request_id; the response carries the same value
    # B1 propagates this to nodes for stale-allocation reclamation.
    coordinator_generation: str | None = None