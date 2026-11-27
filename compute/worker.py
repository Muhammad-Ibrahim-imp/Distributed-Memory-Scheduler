"""Bounded, registry-based execution for explicitly registered handlers."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from common.interfaces.dsm_api import DSMAPI
from compute.task import ComputeResult, ComputeTask, TaskStatus, _TRANSITIONS


@dataclass(frozen=True)
class HandlerOutput:
    data: bytes
    metadata: dict[str, Any]


Handler = Callable[[tuple[bytes, ...], Mapping[str, Any]], HandlerOutput | bytes]
Authorizer = Callable[[Any, ComputeTask], DSMAPI]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, operation: str, handler: Handler) -> None:
        if not operation or not callable(handler):
            raise ValueError("operation and callable handler are required")
        if operation in self._handlers:
            raise ValueError(f"handler already registered for {operation!r}")
        self._handlers[operation] = handler

    def resolve(self, operation: str) -> Handler:
        try:
            return self._handlers[operation]
        except KeyError:
            raise ValueError(f"unsupported operation {operation!r}") from None

    @property
    def operations(self) -> frozenset[str]:
        return frozenset(self._handlers)


class ComputeWorker:
    """Executes only registered handlers and gets DSM access from trusted auth context."""

    def __init__(
        self,
        node_id: str,
        registry: HandlerRegistry,
        authorize: Authorizer,
        max_workers: int = 1,
        max_working_memory_bytes: int = 0,
    ) -> None:
        if not node_id or max_workers <= 0 or max_working_memory_bytes < 0:
            raise ValueError("invalid worker capacity")
        self.node_id = node_id
        self.registry = registry
        self.authorize = authorize
        self.max_workers = max_workers
        self.max_working_memory_bytes = max_working_memory_bytes
        self._slots = asyncio.Semaphore(max_workers)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"compute-{node_id}")
        self._states: dict[str, TaskStatus] = {}
        self._state_lock = asyncio.Lock()
        self._memory_used = 0
        self._memory_changed = asyncio.Condition()

    async def _transition(self, task_id: str, status: TaskStatus) -> None:
        async with self._state_lock:
            current = self._states[task_id]
            if status not in _TRANSITIONS[current]:
                raise RuntimeError(f"invalid task transition {current} -> {status}")
            self._states[task_id] = status

    async def status(self, task_id: str) -> TaskStatus | None:
        async with self._state_lock:
            return self._states.get(task_id)

    async def execute(self, task: ComputeTask, security_context: Any) -> ComputeResult:
        async with self._state_lock:
            if task.task_id in self._states:
                raise ValueError(f"duplicate task_id {task.task_id!r}")
            self._states[task.task_id] = TaskStatus.PENDING
        output_id: str | None = None
        execution_started_at = time.time()
        try:
            handler = self.registry.resolve(task.operation)
            if self.max_working_memory_bytes:
                if task.estimated_memory_bytes <= 0:
                    raise MemoryError("task must declare working memory when worker limit is enabled")
                if task.estimated_memory_bytes > self.max_working_memory_bytes:
                    raise MemoryError("task exceeds worker working-memory limit")
            dsm = self.authorize(security_context, task)
            await self._transition(task.task_id, TaskStatus.QUEUED)
            async with self._slots:
                if self.max_working_memory_bytes:
                    async with self._memory_changed:
                        await self._memory_changed.wait_for(
                            lambda: self._memory_used + task.estimated_memory_bytes <= self.max_working_memory_bytes
                        )
                        self._memory_used += task.estimated_memory_bytes
                await self._transition(task.task_id, TaskStatus.RUNNING)
                started = time.perf_counter()
                try:
                    inputs = tuple([await dsm.read(oid) for oid in task.input_object_ids])
                    loop = asyncio.get_running_loop()
                    value = await loop.run_in_executor(self._executor, handler, inputs, task.parameters)
                    result = value if isinstance(value, HandlerOutput) else HandlerOutput(value, {})
                    if not isinstance(result.data, bytes):
                        raise TypeError("compute handler must return bytes or HandlerOutput")
                    try:
                        json.dumps(result.metadata, allow_nan=False)
                    except (TypeError, ValueError) as exc:
                        raise TypeError("compute handler metadata must be JSON-compatible") from exc
                    output_id = await dsm.alloc(len(result.data))
                    await dsm.write(output_id, result.data)
                    seal = getattr(dsm, "seal", None)
                    if callable(seal):
                        await seal(output_id)
                    elapsed = time.perf_counter() - started
                finally:
                    if self.max_working_memory_bytes:
                        async with self._memory_changed:
                            self._memory_used -= task.estimated_memory_bytes
                            self._memory_changed.notify_all()
            await self._transition(task.task_id, TaskStatus.COMPLETED)
            return ComputeResult(
                task_id=task.task_id,
                node_id=self.node_id,
                status=TaskStatus.COMPLETED,
                output_object_ids=(output_id,),
                output_metadata=result.metadata,
                metrics={"duration_seconds": elapsed, "input_bytes": float(sum(map(len, inputs))),
                         "output_bytes": float(len(result.data)),
                         **getattr(dsm, "metrics", {})},
                operation=task.operation,
                worker_hostname=socket.gethostname(),
                worker_pid=os.getpid(),
                execution_started_at=execution_started_at,
                execution_finished_at=time.time(),
            )
        except (Exception, asyncio.CancelledError) as exc:
            if output_id is not None:
                try:
                    dsm = self.authorize(security_context, task)
                    await dsm.free(output_id)
                except Exception:
                    pass
            state = await self.status(task.task_id)
            if state in (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING):
                await self._transition(task.task_id, TaskStatus.CANCELLED if isinstance(exc, asyncio.CancelledError) else TaskStatus.FAILED)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return ComputeResult(
                task_id=task.task_id, node_id=self.node_id, status=TaskStatus.FAILED, error=str(exc),
                operation=task.operation, worker_hostname=socket.gethostname(), worker_pid=os.getpid(),
                execution_started_at=execution_started_at, execution_finished_at=time.time(),
            )

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)
