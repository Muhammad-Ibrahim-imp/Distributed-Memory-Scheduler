"""Serializable, application-neutral compute task and result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
from math import isfinite
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.QUEUED, TaskStatus.CANCELLED},
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


@dataclass(frozen=True)
class ComputeTask:
    task_id: str
    workload_id: str
    operation: str
    input_object_ids: tuple[str, ...]
    parameters: dict[str, Any] = field(default_factory=dict)
    estimated_memory_bytes: int = 0
    estimated_compute_cost: float = 0.0
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v for v in (self.task_id, self.workload_id, self.operation)):
            raise ValueError("task_id, workload_id, and operation must be non-empty strings")
        if not isinstance(self.input_object_ids, tuple) or any(not isinstance(v, str) or not v for v in self.input_object_ids):
            raise ValueError("input_object_ids must be a tuple of non-empty strings")
        if not isinstance(self.dependencies, tuple) or any(not isinstance(v, str) or not v for v in self.dependencies):
            raise ValueError("dependencies must be a tuple of non-empty strings")
        if self.task_id in self.dependencies:
            raise ValueError("a task cannot depend on itself")
        if (
            isinstance(self.estimated_memory_bytes, bool)
            or not isinstance(self.estimated_memory_bytes, int)
            or self.estimated_memory_bytes < 0
        ):
            raise ValueError("estimated_memory_bytes must be non-negative")
        if (
            isinstance(self.estimated_compute_cost, bool)
            or not isinstance(self.estimated_compute_cost, (int, float))
            or not isfinite(self.estimated_compute_cost)
            or self.estimated_compute_cost < 0
        ):
            raise ValueError("estimated_compute_cost must be non-negative")
        if not isinstance(self.parameters, dict):
            raise ValueError("parameters must be a dictionary")
        try:
            json.dumps(self.parameters, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("parameters must contain only JSON-compatible values") from exc


@dataclass(frozen=True)
class ComputeResult:
    task_id: str
    node_id: str
    status: TaskStatus
    output_object_ids: tuple[str, ...] = ()
    output_metadata: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    operation: str = ""
    worker_hostname: str = ""
    worker_pid: int = 0
    execution_started_at: float = 0.0
    execution_finished_at: float = 0.0
