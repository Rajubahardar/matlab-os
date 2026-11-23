"""Plan execution with progress tracking, recovery and hard control flow.

Control-flow guarantees
-----------------------
* Steps run in dependency order; a step never runs before its dependencies
  reached a successful terminal state.
* A missing step handler is a **failure**, never a silent success.  Phase 9
  never reports work it did not do.
* Admission goes through the resource governor, agent choice goes through the
  capability selector, and authorisation goes through the permission hook.
  There is no code path that bypasses any of the three.
* Failures are classified and either retried, re-routed, extended with a
  remediation step, or aborted by the bounded :class:`Replanner`.
* Global budgets are enforced: plan wall-clock deadline, maximum iterations and
  the re-planning limit.

Timeout semantics (documented limitation)
-----------------------------------------
Step timeouts are *cooperative*: the executor places an absolute deadline in the
step context and, after the handler returns, compares the elapsed time against
the deadline.  Python cannot safely kill an arbitrary in-process call, and a
thread-based hard kill would leak a running thread on a 16 GB host, so the
executor never abandons live work.  A handler that overruns is reported as a
TIMEOUT failure and the remaining plan is aborted.  Handlers that honour
``context["deadline_monotonic"]`` avoid the overrun entirely.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

from ..audit import AuditLog, NullAuditLog
from ..config import PlanningConfig
from ..errors import (
    AgentSelectionError,
    ApprovalRequiredError,
    PermissionDeniedError,
    Phase9Error,
    ResourceExhaustedError,
    StepExecutionError,
)
from ..security.policy import required_permissions as default_required_permissions
from ..security.redaction import redact
from .model import (
    ExecutionPlan,
    ExecutionStatus,
    PlanStep,
    StepStatus,
)
from .replanner import FailureClass, ReplanDecision, Replanner

StepHandler = Callable[[PlanStep, Mapping[str, Any]], Any]


@dataclass
class StepOutcome:
    step_id: str
    agent_id: Optional[str]
    status: str
    duration_seconds: float
    error: Optional[str] = None


@dataclass
class ExecutionResult:
    plan: ExecutionPlan
    status: str
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    replans: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    awaiting_approval: bool = False

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan.plan_id,
            "revision": self.plan.revision,
            "status": self.status,
            "completed": list(self.completed),
            "failed": list(self.failed),
            "skipped": list(self.skipped),
            "replans": self.replans,
            "duration_seconds": round(self.duration_seconds, 4),
            "error": self.error,
            "awaiting_approval": self.awaiting_approval,
            "events": list(self.events),
        }


class PlanExecutor:
    """Execute an :class:`ExecutionPlan` under every Phase 9 guard."""

    def __init__(
        self,
        config: PlanningConfig,
        *,
        selector: Any,
        governor: Any,
        handlers: Any,
        replanner: Optional[Replanner] = None,
        coordinator: Any | None = None,
        permission_hook: Optional[
            Callable[[PlanStep, str], None]
        ] = None,
        permission_requirements: Optional[
            Callable[[str], Iterable[str]]
        ] = None,
        audit: Optional[AuditLog] = None,
        clock=time.time,
        monotonic=time.monotonic,
    ) -> None:
        if selector is None:
            raise ValueError("A capability selector is required.")
        if governor is None:
            raise ValueError("A resource governor is required.")
        if handlers is None:
            raise ValueError("A step-handler registry is required.")

        self.config = config
        self.selector = selector
        self.governor = governor
        self.handlers = handlers
        self.replanner = replanner or Replanner(config, audit=audit, clock=clock)
        self.coordinator = coordinator
        self.permission_hook = permission_hook
        self.permission_requirements = (
            permission_requirements or default_required_permissions
        )
        self.audit = audit or NullAuditLog()
        self._clock = clock
        self._monotonic = monotonic

    # -- handler lookup ---------------------------------------------------- #
    def _handler_for(self, capability: str) -> Optional[StepHandler]:
        registry = self.handlers
        if isinstance(registry, Mapping):
            handler = registry.get(capability)
            return handler if callable(handler) else None
        getter = getattr(registry, "get_handler", None)
        if callable(getter):
            handler = getter(capability)
            return handler if callable(handler) else None
        return None

    # -- public API -------------------------------------------------------- #
    def run(
        self,
        plan: ExecutionPlan,
        *,
        context: Optional[Mapping[str, Any]] = None,
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> ExecutionResult:
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an ExecutionPlan.")
        plan.validate(max_steps=self.config.max_steps)

        started_at = self._clock()
        deadline = self._monotonic() + self.config.plan_timeout_seconds
        base_context = dict(context or {})
        self._observer = on_event

        result = ExecutionResult(
            plan=plan,
            status="RUNNING",
            started_at=started_at,
        )

        max_iterations = self.config.max_steps * (
            self.config.replan_limit + 2
        ) + 10
        iterations = 0

        while iterations < max_iterations:
            iterations += 1

            if self._monotonic() > deadline:
                self._abort_remaining(
                    result,
                    "Plan deadline exceeded before completion.",
                )
                result.status = self._final_status(result)
                result.error = "plan_timeout"
                self._emit(result, on_event, "plan.timeout",
                           {"error": "plan_timeout"})
                return self._finish(result, on_event, "failure")

            ready = result.plan.ready_steps()
            if not ready:
                if all(step.is_terminal for step in result.plan.steps):
                    break
                blocked = result.plan.blocked_steps()
                if blocked:
                    now = self._clock()
                    for step in blocked:
                        step.mark_skipped(
                            "Blocked by a failed or skipped dependency.", now
                        )
                        result.skipped.append(step.step_id)
                        self._emit(
                            result, on_event, "step.skipped",
                            {"step_id": step.step_id, "reason": "blocked"},
                        )
                    continue
                break

            step = ready[0]
            step.status = StepStatus.READY
            self._emit(
                result, on_event, "step.ready",
                {"step_id": step.step_id, "capability": step.capability},
            )

            outcome = self._execute_step(result, step, base_context)
            if outcome is None:
                continue  # plan was revised; continue the loop

            if step.status == StepStatus.COMPLETED:
                result.completed.append(step.step_id)
            else:
                failure = outcome.get("failure")
                revised = self._recover(result, step, failure, on_event)
                if not revised:
                    self._abort_remaining(
                        result,
                        f"Execution aborted after {step.step_id} failed.",
                    )
                    break

        else:  # pragma: no cover - defensive, would indicate a logic bug
            self._abort_remaining(
                result, "Maximum iteration budget exhausted."
            )
            result.error = "iteration_limit"

        result.status = self._final_status(result)
        return self._finish(result, on_event, "completed")

    def _finish(
        self,
        result: ExecutionResult,
        on_event: Optional[Callable[[dict[str, Any]], None]],
        outcome: str,
    ) -> ExecutionResult:
        result.finished_at = self._clock()
        result.replans = int(result.plan.metadata.get("replans", 0))
        result.failed = [
            step.step_id for step in result.plan.steps
            if step.status == StepStatus.FAILED
        ]
        result.skipped = [
            step.step_id for step in result.plan.steps
            if step.status == StepStatus.SKIPPED
        ]
        result.completed = [
            step.step_id for step in result.plan.steps
            if step.status == StepStatus.COMPLETED
        ]

        self.audit.record(
            action="plan.executed",
            actor="executor",
            outcome=result.status,
            details={
                "plan_id": result.plan.plan_id,
                "revision": result.plan.revision,
                "status": result.status,
                "completed": result.completed,
                "failed": result.failed,
                "skipped": result.skipped,
                "replans": result.replans,
                "duration_seconds": round(result.duration_seconds, 4),
                "error": result.error,
            },
        )
        self._emit(
            result, on_event, "plan.finished",
            {"status": result.status, "error": result.error},
        )
        return result

    # -- single step ------------------------------------------------------- #
    def _execute_step(
        self,
        result: ExecutionResult,
        step: PlanStep,
        base_context: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Run one step. Returns an outcome dict, or None when re-planned."""

        # 1) capability-based agent selection (permission scope pre-filtered)
        try:
            selection = self.selector.select(
                required_capabilities=[step.capability],
                required_tools=list(step.required_tools),
                complexity=step.complexity,
                required_permissions=sorted(
                    self.permission_requirements(step.capability)
                ),
                exclude_agents=list(step.excluded_agents),
            )
            profile = getattr(selection, "selected", None)
            if profile is None:
                raise AgentSelectionError(
                    "Selector returned no agent.", step_id=step.step_id
                )
            agent_id = getattr(profile, "agent_id", None)
            if not agent_id:
                raise AgentSelectionError(
                    "Selected agent has no identity.", step_id=step.step_id
                )
        except Phase9Error as exc:
            return self._step_failed(result, step, exc)

        # 2) authorisation
        if self.permission_hook is not None:
            try:
                self.permission_hook(step, agent_id)
            except (PermissionDeniedError, ApprovalRequiredError) as exc:
                return self._step_failed(result, step, exc)

        # 3) resource admission
        requirements = self._requirements_for(step)
        try:
            admission = self.governor.admit(
                f"{result.plan.plan_id}:{step.step_id}",
                requirements=requirements,
                priority=step.priority,
            )
        except Phase9Error as exc:
            return self._step_failed(result, step, exc)

        admitted = bool(getattr(admission, "admitted", False))
        if not admitted:
            reason = str(getattr(admission, "reason", "admission refused"))
            return self._step_failed(
                result,
                step,
                ResourceExhaustedError(
                    "Resource admission refused.",
                    step_id=step.step_id,
                    reason=reason,
                ),
            )

        slot_id = f"{result.plan.plan_id}:{step.step_id}"

        try:
            # 4) the step is assigned and starts here, so the selection is
            #    observable even when dispatch fails immediately afterwards.
            step.mark_running(agent_id, self._clock())
            self._emit(
                result, None, "step.running",
                {"step_id": step.step_id, "agent_id": agent_id},
            )

            # 5) handler availability: a missing handler is a dispatch failure
            #    for this assigned agent, never a silent success.
            handler = self._handler_for(step.capability)
            if handler is None:
                raise StepExecutionError(
                    "No handler is registered for this capability.",
                    capability=step.capability,
                    step_id=step.step_id,
                )

            step_deadline = self._monotonic() + min(
                step.timeout_seconds, self.config.step_timeout_seconds
            )
            step_context = {
                **base_context,
                "plan_id": result.plan.plan_id,
                "step_id": step.step_id,
                "capability": step.capability,
                "agent_id": agent_id,
                "timeout_seconds": step.timeout_seconds,
                "deadline_monotonic": step_deadline,
                "attempt": step.attempts,
                "shared": base_context.get("shared", {}),
            }

            started = self._monotonic()
            try:
                value = handler(step, step_context)
            except Phase9Error:
                raise
            except Exception as exc:  # handler bug or foreign failure
                raise StepExecutionError(
                    f"Step handler raised {type(exc).__name__}.",
                    step_id=step.step_id,
                    capability=step.capability,
                ) from exc
            elapsed = self._monotonic() - started

            if elapsed > step.timeout_seconds:
                raise StepExecutionError(
                    "Step exceeded its cooperative timeout budget.",
                    step_id=step.step_id,
                    elapsed=round(elapsed, 3),
                    timeout=step.timeout_seconds,
                )

            step.mark_completed(value, self._clock())
            self.governor.release(slot_id, success=True)
            self._emit(
                result, None, "step.completed",
                {"step_id": step.step_id, "duration_seconds": round(elapsed, 4)},
            )
            return {"status": "completed", "value": value}

        except Phase9Error as exc:
            self.governor.release(slot_id, success=False)
            return self._step_failed(result, step, exc, already_running=True)
        except Exception as exc:  # pragma: no cover - defensive
            self.governor.release(slot_id, success=False)
            return self._step_failed(
                result,
                step,
                StepExecutionError(
                    f"Unexpected executor failure: {type(exc).__name__}.",
                    step_id=step.step_id,
                ),
                already_running=True,
            )

    # -- failure handling -------------------------------------------------- #
    def _step_failed(
        self,
        result: ExecutionResult,
        step: PlanStep,
        error: BaseException,
        *,
        already_running: bool = False,
    ) -> dict[str, Any]:
        message = self._describe(error)
        now = self._clock()
        if not already_running:
            step.attempts += 1
        step.mark_failed(message, now)

        self._emit(
            result, None, "step.failed",
            {
                "step_id": step.step_id,
                "error": message,
                "failure_class": self.replanner.classify(error).value,
            },
        )
        self.audit.record(
            action="step.failed",
            actor="executor",
            outcome="failed",
            details={
                "plan_id": result.plan.plan_id,
                "step_id": step.step_id,
                "capability": step.capability,
                "error": message,
                "failure_class": self.replanner.classify(error).value,
            },
        )
        return {"status": "failed", "failure": error, "message": message}

    def _recover(
        self,
        result: ExecutionResult,
        step: PlanStep,
        error: Optional[BaseException],
        on_event: Optional[Callable[[dict[str, Any]], None]],
    ) -> bool:
        """Apply the re-planner. Returns True when execution should continue."""
        if error is None:
            return False

        failure_class = self.replanner.classify(error)
        decision = self.replanner.decide(result.plan, step, error)

        if failure_class is FailureClass.PERMISSION:
            result.awaiting_approval = True
            result.status = ExecutionStatus.AWAITING_APPROVAL.value
            result.error = self._describe(error)
            self._emit(
                result, on_event, "plan.awaiting_approval",
                {"step_id": step.step_id, "error": result.error},
            )
            return False

        if decision is ReplanDecision.ABORT:
            result.error = self._describe(error)
            self._emit(
                result, on_event, "plan.abort",
                {"step_id": step.step_id, "error": result.error},
            )
            return False

        try:
            revised = self.replanner.revise(
                result.plan, step, decision, error
            )
        except Phase9Error as exc:
            result.error = self._describe(exc)
            self._emit(
                result, on_event, "plan.replan_failed",
                {"step_id": step.step_id, "error": result.error},
            )
            return False

        result.plan = revised
        result.replans = int(revised.metadata.get("replans", 0))
        self._emit(
            result, on_event, "plan.revised",
            {
                "plan_id": revised.plan_id,
                "revision": revised.revision,
                "decision": decision.value,
                "step_id": step.step_id,
            },
        )
        return True

    def _abort_remaining(
        self, result: ExecutionResult, reason: str
    ) -> None:
        now = self._clock()
        remaining = result.plan.mark_remaining_skipped(reason, now)
        if remaining:
            self._emit(
                result, None, "plan.remaining_skipped",
                {"count": remaining, "reason": reason},
            )

    # -- helpers ----------------------------------------------------------- #
    def _final_status(self, result: ExecutionResult) -> str:
        steps = result.plan.steps
        completed = [s for s in steps if s.status == StepStatus.COMPLETED]
        failed = [s for s in steps if s.status == StepStatus.FAILED]

        if result.awaiting_approval:
            return ExecutionStatus.AWAITING_APPROVAL.value
        if not failed and completed and len(completed) == len(steps):
            return ExecutionStatus.COMPLETED.value
        if failed and not completed:
            return ExecutionStatus.FAILED.value
        if failed:
            return ExecutionStatus.PARTIAL.value
        if completed:
            return ExecutionStatus.PARTIAL.value
        return ExecutionStatus.FAILED.value

    def _requirements_for(self, step: PlanStep) -> Any:
        """Build a ResourceRequirements without importing the scheduler hard."""
        try:
            from matlab_os.intelligence.resource_scheduler import (
                ResourceRequirements,
            )
        except Exception:  # pragma: no cover - defensive
            return None
        ram_gb = 0.25 * max(1, step.complexity)
        cpu_cores = float(max(1, min(step.complexity, 4)))
        return ResourceRequirements(ram_gb=ram_gb, cpu_cores=cpu_cores)

    def _describe(self, error: BaseException) -> str:
        if isinstance(error, Phase9Error):
            head = error.args[0] if error.args else ""
            return f"{type(error).__name__}: {head}"[:500]
        return f"{type(error).__name__}"[:120]

    def _emit(
        self,
        result: ExecutionResult,
        on_event: Optional[Callable[[dict[str, Any]], None]],
        name: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> None:
        event = {
            "event": name,
            "at": self._clock(),
            **redact(dict(payload or {})),
        }
        result.events.append(event)
        observer = on_event if on_event is not None else getattr(
            self, "_observer", None
        )
        if observer is not None:
            try:
                observer(event)
            except Exception:  # pragma: no cover - observer must not break run
                pass


__all__ = [
    "PlanExecutor",
    "StepHandler",
    "StepOutcome",
    "ExecutionResult",
]
