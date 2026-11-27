"""Client-side compute API over an authenticated RemoteDSMAPI connection."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from common.protocol import dsm_pb2
from compute.task import ComputeResult, ComputeTask, TaskStatus


class NetworkComputeAPI:
    def __init__(self, dsm_api, max_pending: int = 32, poll_interval: float = 0.1,
                 direct_local_reads: bool = True):
        if max_pending <= 0 or poll_interval <= 0:
            raise ValueError("max_pending and poll_interval must be positive")
        if not isinstance(direct_local_reads, bool):
            raise ValueError("direct_local_reads must be a bool")
        self.dsm = dsm_api
        self._max_pending = max_pending
        self.direct_local_reads = direct_local_reads
        self._pending = asyncio.Semaphore(max_pending)
        self._poll_interval = poll_interval
        self._status_poll_lock = asyncio.Lock()
        self._last_status_poll = 0.0
        self._results: dict[str, ComputeResult] = {}
        self._statuses: dict[str, TaskStatus] = {}
        self._watchers: dict[str, asyncio.Task] = {}
        self._slot_held: set[str] = set()

    async def submit(self, task: ComputeTask) -> str:
        await self._pending.acquire()
        try:
            env = self.dsm._env()
            request = env.compute_submit
            request.request_id = f"compute-submit-{uuid4()}"
            request.task_id = task.task_id
            request.workload_id = task.workload_id
            request.operation = task.operation
            request.input_object_ids.extend(task.input_object_ids)
            request.parameters_json = json.dumps(task.parameters, allow_nan=False)
            request.estimated_memory_bytes = task.estimated_memory_bytes
            request.estimated_compute_cost = task.estimated_compute_cost
            request.dependencies.extend(task.dependencies)
            request.direct_local_reads = self.direct_local_reads
            response = await self.dsm._call(env)
            result = self._result(response.compute_response)
            self._slot_held.add(task.task_id)
            self._statuses[task.task_id] = result.status
            if result.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                self._results[task.task_id] = result
                self._release_slot(task.task_id)
            else:
                self._watchers[task.task_id] = asyncio.create_task(self._watch(task.task_id))
        except BaseException:
            self._pending.release()
            raise
        return task.task_id

    def _release_slot(self, task_id: str) -> None:
        if task_id in self._slot_held:
            self._slot_held.remove(task_id)
            self._pending.release()

    async def submit_all(self, tasks) -> tuple[str, ...]:
        tasks = tuple(tasks)
        submitted: list[str] = []
        remaining = list(tasks)
        known = set(self._statuses)
        while remaining:
            ready = [task for task in remaining if set(task.dependencies) <= known]
            if not ready:
                raise ValueError("task batch has missing dependencies or a dependency cycle")
            # Submit a bounded wave in parallel. Dependencies only become
            # eligible in the next wave, preserving dependency ordering.
            wave = ready[:self._max_pending]
            ids = await asyncio.gather(*(self.submit(task) for task in wave))
            submitted.extend(ids)
            known.update(ids)
            ready_ids = {task.task_id for task in wave}
            remaining = [task for task in remaining if task.task_id not in ready_ids]
        return tuple(submitted)


    async def _request(self, field: str, task_id: str) -> ComputeResult:
        env = self.dsm._env()
        request = getattr(env, field)
        request.request_id = f"compute-{field}-{uuid4()}"
        request.task_id = task_id
        response = await self.dsm._call(env)
        return self._result(response.compute_response)

    @staticmethod
    def _result(data) -> ComputeResult:
        metadata = json.loads(data.output_metadata_json or "{}")
        if not isinstance(metadata, dict):
            raise ValueError("coordinator returned invalid output metadata")
        return ComputeResult(
            data.task_id, data.node_id, TaskStatus(data.status),
            tuple(data.output_object_ids), metadata, dict(data.metrics), data.error or None,
            data.operation, data.worker_hostname, int(data.worker_pid),
            data.execution_started_at, data.execution_finished_at,
        )

    async def _watch(self, task_id: str):
        try:
            while True:
                result = await self._request_status(task_id)
                self._statuses[task_id] = result.status
                if result.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                    self._results[task_id] = result
                    return
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._statuses[task_id] = TaskStatus.FAILED
            self._results[task_id] = ComputeResult(
                task_id, "", TaskStatus.FAILED, error=f"compute status polling failed: {exc}"
            )
        finally:
            self._watchers.pop(task_id, None)
            self._release_slot(task_id)

    async def _request_status(self, task_id: str) -> ComputeResult:
        # Many task watchers share one authenticated connection. Space their
        # status RPCs so a large tile batch cannot flood the coordinator.
        async with self._status_poll_lock:
            loop = asyncio.get_running_loop()
            delay = self._poll_interval - (loop.time() - self._last_status_poll)
            if delay > 0:
                await asyncio.sleep(delay)
            result = await self._request("compute_status", task_id)
            self._last_status_poll = loop.time()
            return result

    async def get_status(self, task_id: str) -> TaskStatus | None:
        if task_id not in self._statuses:
            try:
                result = await self._request("compute_status", task_id)
            except Exception:
                return None
            self._statuses[task_id] = result.status
        return self._statuses[task_id]

    async def get_result(self, task_id: str) -> ComputeResult | None:
        result = self._results.get(task_id)
        if result is not None:
            return result
        if task_id not in self._statuses:
            await self.get_status(task_id)
        return self._results.get(task_id)

    async def wait(self, task_id: str) -> ComputeResult:
        while True:
            result = await self.get_result(task_id)
            if result is not None:
                return result
            if task_id not in self._statuses:
                raise KeyError(task_id)
            await asyncio.sleep(self._poll_interval)

    async def cancel(self, task_id: str) -> bool:
        result = await self._request("compute_cancel", task_id)
        self._statuses[task_id] = result.status
        if result.status is TaskStatus.CANCELLED:
            self._results[task_id] = result
            watcher = self._watchers.get(task_id)
            if watcher is not None:
                watcher.cancel()
            elif task_id not in self._watchers:
                # Completed task submissions have already released their slot.
                pass
            return True
        return False
