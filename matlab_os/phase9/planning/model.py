"""Plan data model: steps, dependencies, status and DAG validation.

The plan is a directed acyclic graph.  Every mutation that could produce an
invalid plan goes through :meth:`ExecutionPlan.validate`, and the executor
re-validates before running, so a model-assisted planner cannot smuggle a
malformed or cyclic plan into execution.
"""

from __future__ import annotations

import heapq
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from ..errors import PlanLimitError, PlanValidationError


class StepStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


TERMINAL_STEP_STATUSES = frozenset(
    {StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED}
)

SUCCESSFUL_STEP_STATUSES = frozenset(
    {StepStatus.COMPLETED, StepStatus.SKIPPED}
)


class ExecutionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"


@dataclass
class PlanStep:
    """One executable unit of work inside a plan."""

    step_id: str
    title: str
    capability: str
    description: str = ""
    required_tools: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    priority: int = 0
    parameters: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 120.0
    max_attempts: int = 2
    complexity: int = 1
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    result: Any = None
    error: Optional[str] = None
    assigned_agent: Optional[str] = None
    excluded_agents: tuple[str, ...] = ()
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    rationale: str = ""

    # -- validation -------------------------------------------------------- #
    def validate(self) -> None:
        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise PlanValidationError("step_id must be a non-empty string.")
        if not isinstance(self.title, str) or not self.title.strip():
            raise PlanValidationError(
                "Step title must be a non-empty string.",
                step_id=self.step_id,
            )
        if not isinstance(self.capability, str) or not self.capability.strip():
            raise PlanValidationError(
                "Step capability must be a non-empty string.",
                step_id=self.step_id,
            )
        if self.timeout_seconds <= 0:
            raise PlanValidationError(
                "Step timeout must be positive.", step_id=self.step_id
            )
        if not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise PlanValidationError(
                "max_attempts must be an integer >= 1.", step_id=self.step_id
            )
        if not isinstance(self.complexity, int) or self.complexity < 1:
            raise PlanValidationError(
                "complexity must be an integer >= 1.", step_id=self.step_id
            )
        for dependency in self.depends_on:
            if not isinstance(dependency, str) or not dependency.strip():
                raise PlanValidationError(
                    "Dependency ids must be non-empty strings.",
                    step_id=self.step_id,
                )
            if dependency == self.step_id:
                raise PlanValidationError(
                    "A step cannot depend on itself.", step_id=self.step_id
                )
        for name in self.required_tools:
            if not isinstance(name, str) or not name.strip():
                raise PlanValidationError(
                    "required_tools entries must be non-empty strings.",
                    step_id=self.step_id,
                )
        if not isinstance(self.parameters, dict):
            raise PlanValidationError(
                "Step parameters must be a dictionary.", step_id=self.step_id
            )

    # -- lifecycle helpers ------------------------------------------------- #
    def mark_running(self, agent_id: str, now: float) -> None:
        self.status = StepStatus.RUNNING
        self.assigned_agent = agent_id
        self.attempts += 1
        self.started_at = now
        self.error = None

    def mark_completed(self, result: Any, now: float) -> None:
        self.status = StepStatus.COMPLETED
        self.result = result
        self.error = None
        self.finished_at = now

    def mark_failed(self, error: str, now: float) -> None:
        self.status = StepStatus.FAILED
        self.error = error
        self.finished_at = now

    def mark_skipped(self, reason: str, now: float) -> None:
        self.status = StepStatus.SKIPPED
        self.error = reason
        self.finished_at = now

    def reset_for_retry(self) -> None:
        self.status = StepStatus.PENDING
        self.error = None
        self.started_at = None
        self.finished_at = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STEP_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "capability": self.capability,
            "description": self.description,
            "required_tools": list(self.required_tools),
            "depends_on": list(self.depends_on),
            "priority": self.priority,
            "parameters": self.parameters,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
            "complexity": self.complexity,
            "status": self.status.value,
            "attempts": self.attempts,
            "result": self.result,
            "error": self.error,
            "assigned_agent": self.assigned_agent,
            "excluded_agents": list(self.excluded_agents),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PlanStep":
        if not isinstance(payload, Mapping):
            raise PlanValidationError("Serialised step must be a mapping.")
        try:
            step = cls(
                step_id=str(payload["step_id"]),
                title=str(payload["title"]),
                capability=str(payload["capability"]),
                description=str(payload.get("description", "")),
                required_tools=tuple(payload.get("required_tools") or ()),
                depends_on=tuple(payload.get("depends_on") or ()),
                priority=int(payload.get("priority", 0)),
                parameters=dict(payload.get("parameters") or {}),
                timeout_seconds=float(payload.get("timeout_seconds", 120.0)),
                max_attempts=int(payload.get("max_attempts", 2)),
                complexity=int(payload.get("complexity", 1)),
                status=StepStatus(payload.get("status", "pending")),
                attempts=int(payload.get("attempts", 0)),
                result=payload.get("result"),
                error=payload.get("error"),
                assigned_agent=payload.get("assigned_agent"),
                excluded_agents=tuple(payload.get("excluded_agents") or ()),
                started_at=payload.get("started_at"),
                finished_at=payload.get("finished_at"),
                rationale=str(payload.get("rationale", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"Serialised step is invalid: {exc}"
            ) from exc
        step.validate()
        return step


@dataclass
class ExecutionPlan:
    """An ordered, dependency-aware execution plan."""

    plan_id: str
    task: str
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    source: str = "heuristic"
    rationale: str = ""
    revision: int = 0
    parent_plan_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- identity ---------------------------------------------------------- #
    @classmethod
    def create(
        cls,
        task: str,
        goal: str,
        steps: Iterable[PlanStep],
        *,
        source: str = "heuristic",
        rationale: str = "",
        clock=time.time,
    ) -> "ExecutionPlan":
        return cls(
            plan_id=str(uuid.uuid4()),
            task=str(task),
            goal=str(goal),
            steps=list(steps),
            source=source,
            rationale=rationale,
            created_at=clock(),
        )

    def derived(
        self,
        steps: Iterable[PlanStep],
        *,
        source: Optional[str] = None,
        rationale: Optional[str] = None,
    ) -> "ExecutionPlan":
        """Create the next revision of this plan (used by re-planning)."""
        return ExecutionPlan(
            plan_id=str(uuid.uuid4()),
            task=self.task,
            goal=self.goal,
            steps=list(steps),
            source=source or self.source,
            rationale=rationale if rationale is not None else self.rationale,
            revision=self.revision + 1,
            parent_plan_id=self.plan_id,
            created_at=self.created_at,
            metadata=dict(self.metadata),
        )

    # -- validation -------------------------------------------------------- #
    def validate(self, max_steps: int = 200) -> None:
        if not isinstance(self.task, str) or not self.task.strip():
            raise PlanValidationError("Plan task must be a non-empty string.")
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise PlanValidationError("Plan goal must be a non-empty string.")
        if not isinstance(self.steps, list) or not self.steps:
            raise PlanValidationError("A plan must contain at least one step.")
        if len(self.steps) > max_steps:
            raise PlanLimitError(
                "Plan exceeds the maximum step count.",
                steps=len(self.steps),
                limit=max_steps,
            )

        seen: set[str] = set()
        for step in self.steps:
            if not isinstance(step, PlanStep):
                raise PlanValidationError(
                    "Plan steps must be PlanStep instances."
                )
            step.validate()
            if step.step_id in seen:
                raise PlanValidationError(
                    "Duplicate step id.", step_id=step.step_id
                )
            seen.add(step.step_id)

        for step in self.steps:
            for dependency in step.depends_on:
                if dependency not in seen:
                    raise PlanValidationError(
                        "Unknown dependency.",
                        step_id=step.step_id,
                        dependency=dependency,
                    )

        # Cycle detection (Kahn). Self-dependency already rejected above.
        indegree = {step.step_id: 0 for step in self.steps}
        adjacency: dict[str, list[str]] = {step.step_id: [] for step in self.steps}
        for step in self.steps:
            indegree[step.step_id] = len(set(step.depends_on))
            for dependency in set(step.depends_on):
                adjacency[dependency].append(step.step_id)

        queue = [identifier for identifier, degree in indegree.items() if degree == 0]
        visited = 0
        while queue:
            current = queue.pop()
            visited += 1
            for successor in adjacency[current]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    queue.append(successor)

        if visited != len(self.steps):
            unresolved = sorted(
                identifier
                for identifier, degree in indegree.items()
                if degree > 0
            )
            raise PlanValidationError(
                "Plan contains a dependency cycle.",
                cycle_members=unresolved[:10],
            )

    # -- traversal --------------------------------------------------------- #
    def step(self, step_id: str) -> PlanStep:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise KeyError(f"Unknown step: {step_id}")

    def topological_order(self) -> list[PlanStep]:
        """Deterministic order: highest priority first, then step id."""
        self.validate(max_steps=max(200, len(self.steps)))
        indegree = {step.step_id: len(set(step.depends_on)) for step in self.steps}
        by_id = {step.step_id: step for step in self.steps}
        successors: dict[str, list[str]] = {step.step_id: [] for step in self.steps}
        for step in self.steps:
            for dependency in set(step.depends_on):
                successors[dependency].append(step.step_id)

        heap: list[tuple[int, str]] = [
            (-by_id[identifier].priority, identifier)
            for identifier, degree in indegree.items()
            if degree == 0
        ]
        heapq.heapify(heap)

        ordered: list[PlanStep] = []
        while heap:
            _, identifier = heapq.heappop(heap)
            ordered.append(by_id[identifier])
            for successor in successors[identifier]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    heapq.heappush(
                        heap, (-by_id[successor].priority, successor)
                    )

        if len(ordered) != len(self.steps):
            raise PlanValidationError("Plan contains a dependency cycle.")
        return ordered

    def ready_steps(self) -> list[PlanStep]:
        """Pending steps whose dependencies all reached a success state."""
        ready: list[PlanStep] = []
        for step in self.steps:
            if step.status not in (StepStatus.PENDING, StepStatus.READY):
                continue
            if all(
                self.step(dependency).status in SUCCESSFUL_STEP_STATUSES
                for dependency in step.depends_on
            ):
                ready.append(step)
        ready.sort(key=lambda item: (-item.priority, item.step_id))
        return ready

    def blocked_steps(self) -> list[PlanStep]:
        """Pending steps with at least one failed or skipped dependency."""
        blocked: list[PlanStep] = []
        for step in self.steps:
            if step.status not in (StepStatus.PENDING, StepStatus.READY):
                continue
            for dependency in step.depends_on:
                if self.step(dependency).status in (
                    StepStatus.FAILED,
                    StepStatus.SKIPPED,
                ):
                    blocked.append(step)
                    break
        return blocked

    # -- reporting --------------------------------------------------------- #
    def progress(self) -> dict[str, Any]:
        counts = {status.value: 0 for status in StepStatus}
        for step in self.steps:
            counts[step.status.value] += 1
        total = len(self.steps)
        finished = sum(
            counts[status.value]
            for status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
        )
        return {
            "plan_id": self.plan_id,
            "task": self.task,
            "goal": self.goal,
            "source": self.source,
            "revision": self.revision,
            "total_steps": total,
            "status_counts": counts,
            "finished_steps": finished,
            "percent_complete": (
                round(100.0 * finished / total, 2) if total else 0.0
            ),
            "failed_steps": [
                step.step_id for step in self.steps
                if step.status == StepStatus.FAILED
            ],
            "is_complete": all(step.is_terminal for step in self.steps),
        }

    def mark_remaining_skipped(self, reason: str, now: float) -> int:
        skipped = 0
        for step in self.steps:
            if not step.is_terminal:
                step.mark_skipped(reason, now)
                skipped += 1
        return skipped

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task": self.task,
            "goal": self.goal,
            "steps": [step.to_dict() for step in self.steps],
            "source": self.source,
            "rationale": self.rationale,
            "revision": self.revision,
            "parent_plan_id": self.parent_plan_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExecutionPlan":
        if not isinstance(payload, Mapping):
            raise PlanValidationError("Serialised plan must be a mapping.")
        try:
            plan = cls(
                plan_id=str(payload["plan_id"]),
                task=str(payload["task"]),
                goal=str(payload["goal"]),
                steps=[
                    PlanStep.from_dict(item) for item in payload.get("steps", [])
                ],
                source=str(payload.get("source", "heuristic")),
                rationale=str(payload.get("rationale", "")),
                revision=int(payload.get("revision", 0)),
                parent_plan_id=payload.get("parent_plan_id"),
                created_at=float(payload.get("created_at", 0.0)),
                metadata=dict(payload.get("metadata") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanValidationError(
                f"Serialised plan is invalid: {exc}"
            ) from exc
        plan.validate(max_steps=max(200, len(plan.steps)))
        return plan


__all__ = [
    "StepStatus",
    "ExecutionStatus",
    "TERMINAL_STEP_STATUSES",
    "SUCCESSFUL_STEP_STATUSES",
    "PlanStep",
    "ExecutionPlan",
]
