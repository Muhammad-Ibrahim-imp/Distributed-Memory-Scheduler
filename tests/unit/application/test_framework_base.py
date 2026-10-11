import pytest
from application.framework.base import (
    ALLOCATION, TRANSFER, BaseAdapter, PhaseTimings, TimedWorkload,
)
from application.framework.config import AdapterConfig
from common.types.errors import DSMError, OutOfCapacityError


class Dummy(BaseAdapter):
    fail_execute = False

    async def prepare(self, input_data):
        t = PhaseTimings()
        ids = await self._store_objects(input_data, t)
        return TimedWorkload(object_ids=ids, timings=t)

    async def execute(self, workload):
        if self.fail_execute:
            raise RuntimeError("boom")
        return workload

    async def collect_result(self, workload):
        return await self._read_objects(workload.object_ids, workload.timings)


async def test_run_returns_result_and_frees_everything(dsm):
    assert await Dummy(dsm).run([b"aa", b"bbb"]) == [b"aa", b"bbb"]
    assert dsm.live_ids == set()


async def test_run_cleans_up_when_execute_fails(dsm):
    a = Dummy(dsm); a.fail_execute = True
    with pytest.raises(RuntimeError):
        await a.run([b"x", b"y"])
    assert dsm.live_ids == set()


async def test_store_rolls_back_when_alloc_fails(dsm):
    dsm.inject("alloc", OutOfCapacityError("full"))        # one-shot: one of four allocs fails
    with pytest.raises(OutOfCapacityError):
        await Dummy(dsm).prepare([b"1", b"2", b"3", b"4"])
    assert dsm.live_ids == set()


async def test_store_rolls_back_when_write_fails(dsm):
    dsm.inject("write", DSMError("boom"))
    with pytest.raises(DSMError):
        await Dummy(dsm).prepare([b"1", b"2", b"3"])
    assert dsm.live_ids == set()


async def test_cleanup_is_idempotent_and_reports_failures(dsm):
    a = Dummy(dsm)
    w = await a.prepare([b"x", b"y"])
    dsm.inject("free", DSMError("flaky"))
    failures = await a.cleanup(w)
    assert len(failures) == 1
    assert await a.cleanup(w) == [] and dsm.live_ids == set()


async def test_phases_are_timed_separately(dsm):
    w = await Dummy(dsm).prepare([b"x"])
    assert w.timings.get(ALLOCATION) > 0 and w.timings.get(TRANSFER) > 0


async def test_only_generic_api_used(dsm):
    await Dummy(dsm).run([b"x"])
    assert {c[0] for c in dsm.calls} <= {"alloc", "write", "read", "free"}


async def test_concurrency_limit_is_respected(dsm):
    import asyncio
    live = peak = 0
    real = dsm.alloc

    async def slow(size):
        nonlocal live, peak
        live += 1; peak = max(peak, live)
        await asyncio.sleep(0.01)
        try:
            return await real(size)
        finally:
            live -= 1
    dsm.alloc = slow
    await Dummy(dsm, AdapterConfig(max_concurrency=3)).prepare([b"x"] * 12)
    assert peak <= 3


def test_dsm_api_required():
    with pytest.raises(ValueError):
        Dummy(None)