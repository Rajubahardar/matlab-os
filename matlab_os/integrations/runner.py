"""V1 task runner facade over :class:`matlab_os.orchestration.control_plane.LocalAIOSControlPlane`.

This is the operational entry point for daily use. It does **not** replace the
control plane, the Phase 9 runtime, the workspace manager, the approval gate,
or the audit log. It coordinates them through one consistent interface so a user
or UI can:

* submit a natural-language task for a project;
* observe its lifecycle state;
* retrieve the structured outcome;
* persist relevant project memories;
* associate the run with the existing audit trail.

All execution still flows through the control plane, so planning, agent
selection, permission enforcement, approval, resource admission and audit remain
undivided.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from matlab_os.memory import DatabaseService, MemoryService


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------

class TaskStatus(str):
    """Stable task lifecycle states returned by the V1 runner."""

    SUBMITTED = "SUBMITTED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"


VALID_STATUSES = frozenset(
    {
        TaskStatus.SUBMITTED,
        TaskStatus.PLANNING,
        TaskStatus.RUNNING,
        TaskStatus.WAITING_FOR_APPROVAL,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.PARTIALLY_COMPLETED,
    }
)


_TASK_STATUS_FROM_RUNTIME: dict[str, str] = {
    "PLANNED": TaskStatus.SUBMITTED,
    "RUNNING": TaskStatus.RUNNING,
    "AWAITING_APPROVAL": TaskStatus.WAITING_FOR_APPROVAL,
    "COMPLETED": TaskStatus.COMPLETED,
    "PARTIAL": TaskStatus.PARTIALLY_COMPLETED,
    "FAILED": TaskStatus.FAILED,
}


def _normalise_status(status: str) -> str:
    if status in VALID_STATUSES:
        return status
    return _TASK_STATUS_FROM_RUNTIME.get(status, TaskStatus.FAILED)


# ---------------------------------------------------------------------------
# Task submission input
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskRequest:
    """Input for one task submission."""

    task: str
    project_id: int
    category: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)
    requested_by: str = "operator"
    workspace_root: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.task, str) or not self.task.strip():
            raise ValueError("task must be a non-empty string")
        if not isinstance(self.project_id, int) or self.project_id < 1:
            raise ValueError("project_id must be a positive integer")
        if not isinstance(self.category, str):
            raise ValueError("category must be a string")
        if not isinstance(self.requested_by, str) or not self.requested_by.strip():
            raise ValueError("requested_by must be a non-empty string")
        if not isinstance(self.parameters, Mapping):
            raise ValueError("parameters must be a mapping")


# ---------------------------------------------------------------------------
# Task records
# ---------------------------------------------------------------------------

@dataclass
class TaskRecord:
    """Stable public record for one task."""

    task_id: str
    project_id: int
    task: str
    category: str
    status: str
    requested_by: str
    submitted_at: float
    updated_at: float
    runtime_id: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    awaiting_approval: bool = False
    output_locations: list[str] = field(default_factory=list)
    memory_ids: list[int] = field(default_factory=list)
    audit_ref: Optional[str] = None


@dataclass
class TaskResult:
    """Structured outcome returned from a completed or failed task."""

    task_id: str
    status: str
    runtime_status: str
    selected_agent: Optional[str] = None
    summary: Optional[str] = None
    output: Optional[Any] = None
    output_locations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    approval_required: bool = False
    approval_ticket: Optional[str] = None
    execution_duration_seconds: float = 0.0
    audit_ref: Optional[str] = None
    memory_refs: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class TaskRunner:
    """Thin operational facade over the control plane.

    Usage
    -----

    ::

        runner = TaskRunner(control_plane)
        task = runner.submit(TaskRequest(task="...", project_id=1))
        status = runner.status(task.task_id)
        result = runner.result(task.task_id)
    """

    def __init__(
        self,
        control_plane: Any,
        *,
        memory: Optional[MemoryService] = None,
        database: Optional[DatabaseService] = None,
        clock: Optional[Any] = None,
    ) -> None:
        if control_plane is None:
            raise ValueError("control_plane is required")
        self._cp = control_plane
        self._memory = memory
        self._db = database
        self._clock = clock if clock is not None else time.time
        self._tasks: dict[str, TaskRecord] = {}

    # -- submission ------------------------------------------------------ #

    def submit(self, request: TaskRequest) -> TaskRecord:
        now = self._clock()
        task_id = self._task_id()
        # placeholder kept for readability; the real record is built below

        runtime_id = self._cp.runtime.submit(
            request.task.strip(),
            context=dict(request.parameters),
            requested_by=request.requested_by,
        ).submission_id

        record = TaskRecord(
            task_id=task_id,
            project_id=request.project_id,
            task=request.task.strip(),
            category=request.category.strip(),
            status=TaskStatus.SUBMITTED,
            requested_by=request.requested_by,
            submitted_at=now,
            updated_at=now,
            runtime_id=runtime_id,
        )
        self._tasks[task_id] = record
        self._memory_safe(
            request.project_id,
            "task.submitted",
            f"Task submitted: {task_id}",
            author=request.requested_by,
        )
        self._audit(record, "task.submitted", status="submitted")
        return record

    # -- status --------------------------------------------------------- #

    def status(self, task_id: str) -> TaskRecord:
        if task_id not in self._tasks:
            raise KeyError(f"Unknown task: {task_id}")
        record = self._tasks[task_id]
        runtime = self._cp.runtime.submission(record.runtime_id)
        runtime_status = runtime.status
        status = _normalise_status(runtime_status)
        awaiting = runtime_status == "AWAITING_APPROVAL"
        record.status = status
        record.updated_at = self._clock()
        record.awaiting_approval = awaiting
        if awaiting and not record.audit_ref:
            self._audit(record, "task.awaiting_approval", status="awaiting_approval")
        return record

    def result(self, task_id: str) -> TaskResult:
        if task_id not in self._tasks:
            raise KeyError(f"Unknown task: {task_id}")
        record = self._tasks[task_id]
        runtime = self._cp.runtime.submission(record.runtime_id)

        # Execution may not have started yet. Run it only once and cache the
        # runtime result on the record so repeated result() calls stay stable.
        if runtime.result is None:
            try:
                runtime = self._cp.runtime.execute(
                    record.runtime_id,
                    context=dict(getattr(record, "parameters", {}) or {}),
                )
                runtime_submission = self._cp.runtime.submission(record.runtime_id)
                record.status = _normalise_status(runtime_submission.status)
                record.updated_at = self._clock()
                record.awaiting_approval = runtime_submission.status == "AWAITING_APPROVAL"
                if runtime_submission.status == "AWAITING_APPROVAL":
                    self._audit(record, "task.awaiting_approval", status="awaiting_approval")
            except Exception as exc:
                record.status = TaskStatus.FAILED
                record.updated_at = self._clock()
                record.error = str(exc)
                return TaskResult(
                    task_id=task_id,
                    status=TaskStatus.FAILED,
                    runtime_status="UNKNOWN",
                    errors=[str(exc)],
                    approval_required=False,
                    execution_duration_seconds=0.0,
                    audit_ref=record.audit_ref,
                )

        runtime_submission = self._cp.runtime.submission(record.runtime_id)
        runtime_status = runtime_submission.status
        status = _normalise_status(runtime_status)
        record.status = status
        record.updated_at = self._clock()
        result = runtime_submission.result

        selected_agent: Optional[str] = None
        if runtime.plan and runtime.plan.steps:
            step = runtime.plan.steps[-1]
            if step.assigned_agent:
                selected_agent = step.assigned_agent
            if step.result is not None:
                result = step.result

        output_locations, errors, warnings = self._classify_result(
            record, result, runtime_status
        )

        if status in {TaskStatus.COMPLETED, TaskStatus.PARTIALLY_COMPLETED}:
            self._persist_completed(record, result, output_locations)

        return TaskResult(
            task_id=task_id,
            status=status,
            runtime_status=runtime_status,
            selected_agent=selected_agent,
            summary=self._summary(result),
            output=self._output_field(result),
            output_locations=output_locations,
            errors=errors,
            warnings=warnings,
            approval_required=record.awaiting_approval,
            approval_ticket=None,
            execution_duration_seconds=self._duration(runtime),
            audit_ref=record.audit_ref or None,
            memory_refs=list(record.memory_ids),
        )

    # -- public introspection ------------------------------------------- #

    def task(self, task_id: str) -> TaskRecord:
        if task_id not in self._tasks:
            raise KeyError(f"Unknown task: {task_id}")
        return self.status(task_id)

    def tasks(self) -> list[TaskRecord]:
        return [self.status(tid) for tid in list(self._tasks.keys())]

    # -- helpers ------------------------------------------------------- #

    @staticmethod
    def _task_id() -> str:
        import uuid

        return uuid.uuid4().hex

    @staticmethod
    def _duration(runtime: Any) -> float:
        try:
            return float(getattr(runtime.result, "duration_seconds", 0.0))
        except Exception:
            return 0.0

    @staticmethod
    def _output_field(result: Optional[Any]) -> Optional[Any]:
        if result is None:
            return None
        if isinstance(result, Mapping):
            return result.get("output") or result.get("text") or result
        return result

    @staticmethod
    def _summary(result: Optional[Any]) -> Optional[str]:
        if result is None:
            return None
        if isinstance(result, Mapping):
            for key in ("summary", "text", "markdown", "message"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if isinstance(result, str) and result.strip():
            return result.strip()
        return None

    @staticmethod
    def _classify_result(
        record: TaskRecord,
        result: Optional[Any],
        runtime_status: str,
    ) -> tuple[list[str], list[str], list[str]]:
        output_locations: list[str] = []
        errors: list[str] = []
        warnings: list[str] = []

        if isinstance(result, Mapping):
            output = result.get("output")
            if isinstance(output, str) and output.strip():
                warnings.append(str(output))

            files = result.get("files")
            if isinstance(files, list):
                for item in files:
                    if isinstance(item, str) and item.strip():
                        output_locations.append(item)
            outputs = result.get("outputs")
            if isinstance(outputs, list):
                for item in outputs:
                    if isinstance(item, str) and item.strip():
                        output_locations.append(item)

        if runtime_status in {"FAILED", "AWAITING_APPROVAL", "PARTIAL"}:
            if isinstance(result, Mapping):
                err = result.get("error")
                if isinstance(err, str) and err.strip():
                    errors.append(err)
                err_list = result.get("errors")
                if isinstance(err_list, list):
                    for item in err_list:
                        if isinstance(item, str) and item.strip():
                            errors.append(item)
            elif result is None:
                errors.append("No runtime result attached to the submission.")

        return output_locations, errors, warnings

    # -- memory ------------------------------------------------------- #

    def _persist_completed(
        self,
        record: TaskRecord,
        result: Optional[Any],
        output_locations: list[str],
    ) -> None:
        if self._memory is None or self._db is None:
            return
        project_id = record.project_id
        summary = self._summary(result)
        content_parts: list[str] = [record.task]
        if summary:
            content_parts.append(summary)
        if output_locations:
            content_parts.append("Outputs: " + ", ".join(output_locations))
        content = " — ".join(content_parts)

        try:
            mem_id = self._memory.store_episode(
                project_id=project_id,
                content=content,
                author=record.requested_by,
                tags={"task", record.category or "task"},
            )
            record.memory_ids.append(mem_id)
        except Exception as exc:
            self._audit(record, "memory.failed", status="memory_error", error=str(exc))

        try:
            for location in output_locations:
                self._memory.store_fact(
                    project_id=project_id,
                    key=f"task.{record.task_id}.output",
                    content=location,
                    author=record.requested_by,
                )
        except Exception as exc:
            self._audit(record, "memory.failed", status="memory_error", error=str(exc))

    def _memory_safe(
        self,
        project_id: int,
        action: str,
        content: str,
        *,
        author: str = "operator",
        tags: Optional[set[str]] = None,
    ) -> None:
        if self._memory is None or self._db is None:
            return
        try:
            self._memory.store_episode(
                project_id=project_id,
                content=content,
                author=author,
                tags=tags,
            )
        except Exception:
            return

    # -- audit ------------------------------------------------------- #

    def _audit(
        self,
        record: TaskRecord,
        action: str,
        *,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        details: dict[str, Any] = {
            "task_id": record.task_id,
            "project_id": record.project_id,
            "action": action,
            "status": status,
            "runtime_id": record.runtime_id,
        }
        if error:
            details["error"] = error
        try:
            self._cp.runtime.audit.record(
                action=f"v1.runner.{action}",
                actor=record.requested_by,
                outcome=status,
                details=details,
            )
            record.audit_ref = f"v1.runner.{action}"
        except Exception:
            return
