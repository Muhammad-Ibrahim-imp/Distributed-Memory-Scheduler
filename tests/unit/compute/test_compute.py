import pytest

from compute import ComputeAPI, ComputeTask, ComputeWorker, HandlerRegistry, TaskStatus
from tests.mocks.mock_dsm_api import MockDSMAPI


def worker(node_id, dsm, handler):
    registry = HandlerRegistry()
    registry.register("bytes.copy", handler)
    return ComputeWorker(node_id, registry, lambda _auth, _task: dsm, max_workers=1)


@pytest.mark.asyncio
async def test_worker_stores_registered_handler_output_in_dsm():
    dsm = MockDSMAPI()
    source = await dsm.alloc(3)
    await dsm.write(source, b"abc")
    node = worker("node-1", dsm, lambda inputs, _params: inputs[0].upper())
    api = ComputeAPI([node], security_context=object())
    task = ComputeTask("t1", "w1", "bytes.copy", (source,))

    assert await api.submit(task) == "t1"
    result = await api.wait("t1")
    assert result.status is TaskStatus.COMPLETED
    assert result.node_id == "node-1"
    assert await dsm.read(result.output_object_ids[0]) == b"ABC"
    node.close()


@pytest.mark.asyncio
async def test_worker_fails_closed_for_missing_input_and_records_failure():
    dsm = MockDSMAPI()
    node = worker("node-1", dsm, lambda inputs, _params: inputs[0])
    api = ComputeAPI([node], security_context=object())

    await api.submit(ComputeTask("t2", "w1", "bytes.copy", ("missing",)))
    result = await api.wait("t2")
    assert result.status is TaskStatus.FAILED
    assert result.error
    assert await api.get_status("t2") is TaskStatus.FAILED
    node.close()


@pytest.mark.asyncio
async def test_worker_frees_output_allocation_when_result_write_fails():
    dsm = MockDSMAPI()
    source = await dsm.alloc(1)
    await dsm.write(source, b"x")
    dsm.inject("write", RuntimeError("injected output write failure"))
    node = worker("node-1", dsm, lambda _inputs, _params: b"result")
    api = ComputeAPI([node], security_context=object())

    await api.submit(ComputeTask("t4", "w1", "bytes.copy", (source,)))
    result = await api.wait("t4")
    assert result.status is TaskStatus.FAILED
    assert dsm.live_ids == {source}
    node.close()


@pytest.mark.asyncio
async def test_compute_api_prefers_node_holding_most_input_objects():
    dsm = MockDSMAPI()
    left = worker("left", dsm, lambda _inputs, _params: b"left")
    right = worker("right", dsm, lambda _inputs, _params: b"right")
    api = ComputeAPI([left, right], security_context=object(), object_location=lambda oid: "right")
    task = ComputeTask("t3", "w1", "bytes.copy", ("a", "b"))

    await api.submit(task)
    assert (await api.wait("t3")).node_id == "right"
    left.close()
    right.close()


@pytest.mark.asyncio
async def test_submit_all_backpressures_without_deadlocking():
    import asyncio

    dsm = MockDSMAPI()
    node = worker("node-1", dsm, lambda _inputs, _params: b"ok")
    api = ComputeAPI([node], security_context=object(), max_pending=1)
    tasks = [ComputeTask(f"bounded-{i}", "work", "bytes.copy", ()) for i in range(4)]

    await asyncio.wait_for(api.submit_all(tasks), timeout=2)
    results = await asyncio.wait_for(
        asyncio.gather(*(api.wait(task.task_id) for task in tasks)), timeout=2
    )

    assert all(result.status is TaskStatus.COMPLETED for result in results)
    node.close()


@pytest.mark.asyncio
async def test_cancelled_queued_task_releases_backpressure_slot():
    import asyncio

    dsm = MockDSMAPI()
    node = worker("node-1", dsm, lambda _inputs, _params: b"ok")
    api = ComputeAPI([node], security_context=object(), max_pending=1)

    await api.submit(ComputeTask("cancelled", "work", "bytes.copy", ()))
    assert await api.cancel("cancelled")
    await asyncio.sleep(0)
    await asyncio.wait_for(api.submit(ComputeTask("after-cancel", "work", "bytes.copy", ())), timeout=1)
    assert (await asyncio.wait_for(api.wait("after-cancel"), timeout=1)).status is TaskStatus.COMPLETED
    node.close()


def test_registry_rejects_overwriting_an_operation():
    registry = HandlerRegistry()
    registry.register("bytes.copy", lambda _inputs, _params: b"")
    with pytest.raises(ValueError, match="already registered"):
        registry.register("bytes.copy", lambda _inputs, _params: b"")

