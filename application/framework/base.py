"""Shared plumbing for concrete adapters. Builds on common.interfaces.ApplicationAdapter
(which already provides run(), cleanup() and the DSM injection).

Adds: config, bounded-concurrency DSM helpers that roll back on failure, and per-phase
timings so T_chunking / T_allocation / T_transfer / T_processing / T_aggregation can be
measured separately (spec §33, Principle 6). Uses only the generic DSMAPI (Principle 1).
"""
from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Sequence

from application.framework.config import AdapterConfig
from common.interfaces.application_adapter import ApplicationAdapter, PreparedWorkload
from common.interfaces.dsm_api import DSMAPI

CHUNKING, ALLOCATION, TRANSFER, PROCESSING, AGGREGATION = (
    "chunking", "allocation", "transfer", "processing", "aggregation")


class PhaseTimings:
    def __init__(self) -> None:
        self._t: dict[str, float] = {}

    @contextmanager
    def measure(self, phase: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self._t[phase] = self._t.get(phase, 0.0) + (time.perf_counter() - start)

    def get(self, phase: str) -> float:
        return self._t.get(phase, 0.0)

    def as_dict(self) -> dict[str, float]:
        return dict(self._t)


@dataclass
class TimedWorkload(PreparedWorkload):
    timings: PhaseTimings = field(default_factory=PhaseTimings)


class BaseAdapter(ApplicationAdapter):
    def __init__(self, dsm_api: DSMAPI, config: AdapterConfig | None = None) -> None:
        super().__init__(dsm_api)
        self.config = config or AdapterConfig()
        self._sem = asyncio.Semaphore(self.config.max_concurrency)

    async def _bounded(self, coro):
        async with self._sem:
            return await coro

    async def _store_objects(self, payloads: Sequence[bytes], timings: PhaseTimings) -> list[str]:
        """alloc all, then write all. On ANY failure free what was allocated and re-raise
        (the interface requires this: no workload is returned to clean up)."""
        ids: list[str] = []
        try:
            with timings.measure(ALLOCATION):
                res = await asyncio.gather(*(self._bounded(self.dsm.alloc(len(p))) for p in payloads),
                                           return_exceptions=True)
            ids = [r for r in res if isinstance(r, str)]
            errs = [r for r in res if isinstance(r, BaseException)]
            if errs:
                raise errs[0]
            with timings.measure(TRANSFER):
                res = await asyncio.gather(*(self._bounded(self.dsm.write(i, p))
                                             for i, p in zip(ids, payloads)), return_exceptions=True)
            errs = [r for r in res if isinstance(r, BaseException)]
            if errs:
                raise errs[0]
            return ids
        except BaseException:
            await self.cleanup(PreparedWorkload(object_ids=ids))
            raise

    async def _read_objects(self, object_ids: Sequence[str], timings: PhaseTimings) -> list[bytes]:
        with timings.measure(TRANSFER):
            return list(await asyncio.gather(*(self._bounded(self.dsm.read(i)) for i in object_ids)))