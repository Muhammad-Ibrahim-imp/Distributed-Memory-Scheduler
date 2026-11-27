"""Bounded async compute API over registered workers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from compute.task import ComputeResult, ComputeTask, TaskStatus
from compute.worker import ComputeWorker


class ComputeAPIClient(Protocol):
    async def submit_all(self, tasks: Sequence[ComputeTask]) -> tuple[str, ...]: ...

    async def wait(self, task_id: str) -> ComputeResult: ...


class ComputeAPI:
    def __init__(
        self,
        workers: Sequence[ComputeWorker],
        security_context: Any,
        max_pending: int = 32,
        object_location: Callable[[str], str | None] | None = None,
    ) -> None:
        if not workers or max_pending <= 0:
            raise ValueError("at least one worker and positive max_pending are required")
        if len({worker.node_id for worker in workers}) != len(workers):
            raise ValueError("worker node_id values must be unique")
        self._workers = tuple(workers)
        self._security_context = security_context
        self._object_location = object_location or (lambda _oid: None)
        self._pending = asyncio.Semaphore(max_pending)
        self._results: dict[str, ComputeResult] = {}
        self._tasks: dict[str, asyncio.Task[ComputeResult]] = {}
        self._active: dict[str, int] = {worker.node_id: 0 for worker in workers}
        self._lock = asyncio.Lock()

    def _choose_worker(self, task: ComputeTask) -> ComputeWorker:
        locality: dict[str, int] = {}
        for oid in task.input_object_ids:
            node_id = self._object_location(oid)
            if node_id:
                locality[node_id] = locality.get(node_id, 0) + 1
        return max(
            self._workers,
            key=lambda w: (locality.get(w.node_id, 0), -self._active[w.node_id], w.node_id),
        )

    async def submit(self, task: ComputeTask) -> str:
        await self._pending.acquire()
        try:
            async with self._lock:
                if task.task_id in self._tasks:
                    raise ValueError(f"duplicate task_id {task.task_id!r}")
                missing = set(task.dependencies).difference(self._tasks)
                if missing:
                    raise ValueError(f"dependencies must be submitted first: {sorted(missing)}")
                future = asyncio.create_task(self._run(task))
                future.add_done_callback(lambda _future: self._pending.release())
                self._tasks[task.task_id] = future
        except BaseException:
            self._pending.release()
            raise
        return task.task_id

    async def _run(self, task: ComputeTask) -> ComputeResult:
        try:
            for dependency in task.dependencies:
                result = await self.wait(dependency)
                if result.status is not TaskStatus.COMPLETED:
                    raise RuntimeError(f"dependency {dependency!r} did not complete")
            worker = self._choose_worker(task)
            async with self._lock:
                self._active[worker.node_id] += 1
            try:
                result = await worker.execute(task, self._security_context)
            finally:
                async with self._lock:
                    self._active[worker.node_id] -= 1
            self._results[task.task_id] = result
            return result
        except asyncio.CancelledError:
            result = ComputeResult(task.task_id, "", TaskStatus.CANCELLED)
            self._results[task.task_id] = result
            raise
        except Exception as exc:
            result = ComputeResult(task.task_id, "", TaskStatus.FAILED, error=str(exc))
            self._results[task.task_id] = result
            return result

    async def submit_all(self, tasks: Sequence[ComputeTask]) -> tuple[str, ...]:
        task_ids = []
        for task in tasks:
            task_ids.append(await self.submit(task))
        return tuple(task_ids)

    async def get_status(self, task_id: str) -> TaskStatus | None:
        result = self._results.get(task_id)
        if result:
            return result.status
        future = self._tasks.get(task_id)
        if not future:
            return None
        if future.done():
            if future.cancelled():
                return self._results[task_id].status
            return future.result().status
        for worker in self._workers:
            status = await worker.status(task_id)
            if status is not None:
                return status
        return TaskStatus.QUEUED

    async def get_result(self, task_id: str) -> ComputeResult | None:
        return self._results.get(task_id)

    async def wait(self, task_id: str) -> ComputeResult:
        future = self._tasks.get(task_id)
        if future is None:
            raise KeyError(task_id)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            if future.cancelled():
                return self._results[task_id]
            raise

    async def cancel(self, task_id: str) -> bool:
        future = self._tasks.get(task_id)
        if future is None or future.done():
            return False
        future.cancel()
        self._results[task_id] = ComputeResult(task_id, "", TaskStatus.CANCELLED)
        return True
