"""The contract every application adapter implements.

Rules:
  * Adapters talk to the DSM only through the injected `dsm_api` (a DSMAPI).
    They never import DSM internals.
  * Data flows through explicit `PreparedWorkload` handles, not hidden state
    on `self`, so two workloads can run on the same adapter at once.
  * `chunk()` / `aggregate()` are NOT part of this interface. Nothing outside
    an adapter calls them, so each adapter keeps its own (tests call them
    directly on the concrete adapter).
  * If `prepare()` fails part-way it must free what it already allocated
    before re-raising (use `self.cleanup()`), because no workload is returned
    for the caller to clean up.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from common.interfaces.dsm_api import DSMAPI
from common.types.errors import DSMError


@dataclass
class PreparedWorkload:
    """DSM objects for one workload plus what is needed to rebuild the result.

    layout[object_id] holds adapter-specific placement info, for example
    {"row": 0, "col": 1, "shape": (256, 256, 3), "dtype": "uint8"}.
    """

    object_ids: list[str]
    layout: dict[str, dict[str, Any]] = field(default_factory=dict)


class ApplicationAdapter(ABC):
    def __init__(self, dsm_api: DSMAPI):
        if dsm_api is None:
            raise ValueError("dsm_api is required")
        self.dsm = dsm_api                      # injected; never imported directly

    @abstractmethod
    async def prepare(self, input_data: Any) -> PreparedWorkload:
        """Split the input, allocate and write it through self.dsm."""

    @abstractmethod
    async def execute(self, workload: PreparedWorkload) -> PreparedWorkload:
        """Process the objects through self.dsm; return the processed objects."""

    @abstractmethod
    async def collect_result(self, workload: PreparedWorkload) -> Any:
        """Read the processed objects, combine them and return the result."""

    def estimate_memory(self, input_data: Any) -> int | None:
        """Estimated bytes the workload needs, or None if unknown.
        Called by other members' components (A1's capacity experiment, B2)."""
        return None

    async def cleanup(self, *workloads: PreparedWorkload | None) -> list[tuple[str, DSMError]]:
        """Free every object in the given workloads.

        free() is idempotent, so calling this twice, or on ids that were
        never written, is safe. DSMError is caught per id, so one failure
        (say a rate-limit error) never skips the rest. Returns the failures.
        """
        failures: list[tuple[str, DSMError]] = []
        seen: set[str] = set()
        for workload in workloads:
            if workload is None:
                continue
            for oid in workload.object_ids:
                if oid in seen:
                    continue
                seen.add(oid)
                try:
                    await self.dsm.free(oid)
                except DSMError as exc:
                    failures.append((oid, exc))
        return failures

    async def run(self, input_data: Any) -> Any:
        """prepare -> execute -> collect_result, always cleaning up."""
        workload = await self.prepare(input_data)
        processed: PreparedWorkload | None = None
        try:
            processed = await self.execute(workload)
            return await self.collect_result(processed)
        finally:
            await self.cleanup(workload, processed)