"""Minimal resource scheduler surface required by the current branch.

The scheduler is a small task-state machine used by the Phase 9 resource
governor for admission accounting. It is deliberately minimal and focused on
the call patterns currently exercised by this branch.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SchedulerError(Exception):
    """Base class for scheduler-local errors."""


class DuplicateTaskError(SchedulerError):
    """Raised when ``submit`` is called for an already known task id."""


class UnknownTaskError(SchedulerError):
    """Raised when a task id is not known to the scheduler."""


class TaskState(Enum):
    PENDING = "pending"
    WAITING_APPROVAL = "waiting_approval"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass
class ResourceSnapshot:
    cpu_load_percent: float
    ram_available_gb: float
    logical_cpu_count: int
    timestamp: Optional[float] = None
    gpu_available: bool = False
    temperature_c: Optional[float] = None


@dataclass
class ResourceRequirements:
    ram_gb: float = 0.0
    cpu_cores: float = 0.0
    gpu_memory_gb: float = 0.0
    model_name: str = ""
    max_temperature_c: Optional[float] = None


@dataclass
class _TaskRecord:
    task_id: str
    requirements: ResourceRequirements
    priority: int
    state: TaskState = TaskState.WAITING_APPROVAL
    reason: Optional[str] = None


class ResourceScheduler:
    """Small admission-aware task scheduler for the resource governor.

    Parameters
    ----------
    max_concurrent:
        Maximum number of concurrently running tasks.
    max_cpu_load_percent:
        CPU load ceiling used by the governor's pressure checks.
    minimum_free_ram_gb:
        RAM reserve used by the governor's pressure checks.
    thermal_limit_c:
        Optional thermal limit used by the governor's pressure checks.
    """

    def __init__(
        self,
        *,
        max_concurrent: int = 2,
        max_cpu_load_percent: float = 100.0,
        minimum_free_ram_gb: float = 0.0,
        thermal_limit_c: Optional[float] = None,
    ) -> None:
        self.max_concurrent = int(max_concurrent)
        self.max_cpu_load_percent = float(max_cpu_load_percent)
        self.minimum_free_ram_gb = float(minimum_free_ram_gb)
        self.thermal_limit_c = (
            None if thermal_limit_c is None else float(thermal_limit_c)
        )

        self._lock = threading.RLock()
        self._tasks: dict[str, _TaskRecord] = {}
        self._queue: list[str] = []
        self._snapshot: Optional[ResourceSnapshot] = None

    # ------------------------------------------------------------------ #
    # Telemetry
    # ------------------------------------------------------------------ #

    def update_resources(self, snapshot: ResourceSnapshot) -> None:
        """Record the latest telemetry snapshot for later pressure checks."""
        if not isinstance(snapshot, ResourceSnapshot):
            raise SchedulerError("Expected a ResourceSnapshot.")
        with self._lock:
            self._snapshot = snapshot

    # ------------------------------------------------------------------ #
    # Task lifecycle
    # ------------------------------------------------------------------ #

    def submit(
        self,
        task_id: str,
        requirements: ResourceRequirements,
        priority: int,
    ) -> None:
        """Register a new task.

        Raises ``DuplicateTaskError`` when *task_id* is already known.
        """
        if not isinstance(task_id, str) or not task_id.strip():
            raise SchedulerError("task_id must be a non-empty string.")
        task_id = task_id.strip()
        with self._lock:
            if task_id in self._tasks:
                raise DuplicateTaskError(
                    f"Task {task_id!r} is already known."
                )
            self._tasks[task_id] = _TaskRecord(
                task_id=task_id,
                requirements=requirements,
                priority=priority,
                state=TaskState.WAITING_APPROVAL,
            )

    def mark_approved(self, task_id: str) -> None:
        """Move a waiting task into the dispatch queue."""
        with self._lock:
            record = self._get(record=True)
            task = record(task_id)
            if task.state == TaskState.WAITING_APPROVAL:
                task.state = TaskState.QUEUED
                self._queue.append(task_id)
            elif task.state == TaskState.QUEUED:
                pass
            else:
                raise SchedulerError(
                    f"Task {task_id!r} is not in a approveable state "
                    f"({task.state.value})."
                )

    def dispatch_next(self) -> Optional[_TaskRecord]:
        """Dispatch one queued task if a running slot is free."""
        with self._lock:
            if self._running_count() >= self.max_concurrent:
                return None
            while self._queue:
                candidate = self._queue.pop(0)
                record = self._get(record=True)
                task = record(candidate)
                if task.state == TaskState.QUEUED:
                    task.state = TaskState.RUNNING
                    return task
            return None

    def complete(self, task_id: str, *, success: bool = True) -> None:
        """Mark a running task as completed or failed."""
        with self._lock:
            record = self._get(record=True)
            task = record(task_id)
            if success:
                task.state = TaskState.COMPLETED
            else:
                task.state = TaskState.FAILED

    def get_task(self, task_id: str) -> _TaskRecord:
        """Return the task record for *task_id*."""
        with self._lock:
            record = self._get(record=False)
            task = record(task_id)
            return task

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    def status(self) -> dict[str, Any]:
        """Return a small scheduler status snapshot."""
        with self._lock:
            return {
                "running": self._running_count(),
                "tasks": self._active_count(),
                "max_concurrent": self.max_concurrent,
            }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _get(self, record: bool):
        """Return either the task lookup callable or a locked record context."""

        def lookup(task_id: str) -> _TaskRecord:
            task = self._tasks.get(task_id)
            if task is None:
                raise UnknownTaskError(f"Task {task_id!r} is unknown.")
            return task

        if record:
            return lookup
        return lookup

    def _running_count(self) -> int:
        return sum(1 for task in self._tasks.values() if task.state == TaskState.RUNNING)

    def _active_count(self) -> int:
        terminal = {TaskState.COMPLETED, TaskState.FAILED, TaskState.REJECTED}
        return sum(1 for task in self._tasks.values() if task.state not in terminal)
