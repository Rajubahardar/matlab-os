"""Failure classification and bounded re-planning.

Re-planning is deliberately conservative:

* the total number of revisions per execution is bounded by
  ``PlanningConfig.replan_limit``; exceeding it raises
  :class:`ReplanLimitError` instead of looping forever;
* a permission or security failure is **never** auto-retried — it aborts,
  because retrying cannot create an authorisation that does not exist;
* a revision produces a *new* plan object (new ``plan_id``, incremented
  ``revision``, ``parent_plan_id`` pointing at the previous plan) so the audit
  trail keeps every attempt;
* the original plan is never mutated, so callers can compare revisions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from ..audit import AuditLog, NullAuditLog
from ..config import PlanningConfig
from ..errors import (
    ApprovalRejectedError,
    ApprovalRequiredError,
    CollaborationTimeoutError,
    ConcurrencyLimitError,
    ConflictError,
    DelegationDepthExceededError,
    DelegationLimitError,
    IntegrityError,
    PathSecurityError,
    PermissionDeniedError,
    Phase9Error,
    PlanValidationError,
    ProviderAuthError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ReplanLimitError,
    ResourceExhaustedError,
    SecurityViolation,
    StepExecutionError,
    TelemetryUnavailableError,
)
from .model import ExecutionPlan, PlanStep, StepStatus


class FailureClass(str, Enum):
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    RESOURCE = "resource"
    PERMISSION = "permission"
    PERMANENT = "permanent"


class ReplanDecision(str, Enum):
    RETRY = "retry"
    REROUTE = "reroute"
    EXTEND = "extend"
    ABORT = "abort"


_SECURITY_ERRORS = (
    PermissionDeniedError,
    ApprovalRequiredError,
    ApprovalRejectedError,
    PathSecurityError,
    SecurityViolation,
    IntegrityError,
)

_TIMEOUT_ERRORS = (
    ProviderTimeoutError,
    CollaborationTimeoutError,
    TimeoutError,
)

_RESOURCE_ERRORS = (
    ResourceExhaustedError,
    ConcurrencyLimitError,
    TelemetryUnavailableError,
)

_TRANSIENT_ERRORS = (
    ProviderRateLimitError,
    ProviderUnavailableError,
    StepExecutionError,
)

_PERMANENT_ERRORS = (
    ProviderAuthError,
    ProviderNotConfiguredError,
    ProviderResponseError,
    PlanValidationError,
    DelegationDepthExceededError,
    DelegationLimitError,
    ConflictError,
)


@dataclass(frozen=True)
class ReplanRecord:
    revision: int
    step_id: str
    failure_class: FailureClass
    decision: ReplanDecision
    reason: str


class Replanner:
    """Classify failures and produce bounded revised plans."""

    def __init__(
        self,
        config: PlanningConfig,
        *,
        audit: Optional[AuditLog] = None,
        clock=time.time,
    ) -> None:
        self.config = config
        self.audit = audit or NullAuditLog()
        self._clock = clock

    # -- classification ---------------------------------------------------- #
    def classify(self, error: BaseException) -> FailureClass:
        if isinstance(error, _SECURITY_ERRORS):
            return FailureClass.PERMISSION
        if isinstance(error, _TIMEOUT_ERRORS):
            return FailureClass.TIMEOUT
        if isinstance(error, _RESOURCE_ERRORS):
            return FailureClass.RESOURCE
        if isinstance(error, _PERMANENT_ERRORS):
            return FailureClass.PERMANENT
        if isinstance(error, _TRANSIENT_ERRORS):
            return FailureClass.TRANSIENT
        if isinstance(error, ProviderError):
            return (
                FailureClass.TRANSIENT if error.retryable
                else FailureClass.PERMANENT
            )
        return FailureClass.PERMANENT

    # -- decision ---------------------------------------------------------- #
    def decide(
        self,
        plan: ExecutionPlan,
        step: PlanStep,
        error: BaseException,
    ) -> ReplanDecision:
        failure = self.classify(error)
        attempts_left = step.attempts < step.max_attempts
        has_alternative_agent = bool(
            step.assigned_agent or step.excluded_agents
        )

        if failure is FailureClass.PERMISSION:
            return ReplanDecision.ABORT
        if failure is FailureClass.PERMANENT:
            return ReplanDecision.ABORT

        if failure is FailureClass.RESOURCE:
            if attempts_left:
                return ReplanDecision.RETRY
            return ReplanDecision.ABORT

        if failure in (FailureClass.TRANSIENT, FailureClass.TIMEOUT):
            if attempts_left:
                return ReplanDecision.RETRY
            if has_alternative_agent and len(plan.steps) < self.config.max_steps:
                return ReplanDecision.REROUTE
            if len(plan.steps) < self.config.max_steps:
                return ReplanDecision.EXTEND
            return ReplanDecision.ABORT

        return ReplanDecision.ABORT

    # -- revision ---------------------------------------------------------- #
    def revise(
        self,
        plan: ExecutionPlan,
        step: PlanStep,
        decision: ReplanDecision,
        error: BaseException,
    ) -> ExecutionPlan:
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an ExecutionPlan.")
        if not isinstance(step, PlanStep):
            raise TypeError("step must be a PlanStep.")
        if not isinstance(decision, ReplanDecision):
            raise TypeError("decision must be a ReplanDecision.")

        replans = int(plan.metadata.get("replans", 0))
        if replans >= self.config.replan_limit:
            raise ReplanLimitError(
                "The re-planning budget for this execution is exhausted.",
                replans=replans,
                limit=self.config.replan_limit,
                step_id=step.step_id,
            )

        failure = self.classify(error)
        now = self._clock()
        message = self._describe_error(error)

        steps = [PlanStep.from_dict(item.to_dict()) for item in plan.steps]
        target = self._find(steps, step.step_id)

        if decision is ReplanDecision.RETRY:
            target.reset_for_retry()
            target.status = StepStatus.PENDING
            # A retry does not erase who last attempted the step: the revised
            # plan must still record the assignment that was actually made.
            # The next attempt re-selects an agent and overwrites this value.
            target.assigned_agent = step.assigned_agent
            summary = (
                f"Retry {step.step_id} after a {failure.value} failure "
                f"(attempt {target.attempts} of {target.max_attempts})."
            )

        elif decision is ReplanDecision.REROUTE:
            excluded = set(target.excluded_agents)
            previous = target.assigned_agent or step.assigned_agent
            if previous:
                excluded.add(previous)
            target.excluded_agents = tuple(sorted(excluded))
            target.reset_for_retry()
            # The exclusion list is what prevents re-selection; the revised
            # plan still records which agent last attempted the step.
            target.assigned_agent = previous
            summary = (
                f"Re-route {step.step_id} away from "
                f"{step.assigned_agent or 'the previous agent'} after a "
                f"{failure.value} failure."
            )

        elif decision is ReplanDecision.EXTEND:
            summary = self._extend(plan, steps, target, message, now)

        else:  # ABORT
            target.mark_failed(message, now)
            for item in steps:
                if not item.is_terminal:
                    item.mark_skipped(
                        f"Execution aborted after {step.step_id} failed.",
                        now,
                    )
            summary = (
                f"Abort the plan: {step.step_id} failed with a "
                f"{failure.value} error that re-planning cannot resolve."
            )

        revised = plan.derived(
            steps, rationale=f"{plan.rationale} | revision: {summary}".strip()
        )
        revised.metadata.update(plan.metadata)
        revised.metadata["replans"] = replans + 1
        revised.metadata["last_replan"] = {
            "step_id": step.step_id,
            "failure_class": failure.value,
            "decision": decision.value,
            "reason": summary,
            "error": message,
            "at": now,
        }
        revised.validate(max_steps=self.config.max_steps)

        self.audit.record(
            action="plan.revised",
            actor="replanner",
            outcome=decision.value,
            details={
                "parent_plan_id": plan.plan_id,
                "plan_id": revised.plan_id,
                "revision": revised.revision,
                "step_id": step.step_id,
                "failure_class": failure.value,
                "decision": decision.value,
                "reason": summary,
            },
        )
        return revised

    # -- extension helper -------------------------------------------------- #
    def _extend(
        self,
        plan: ExecutionPlan,
        steps: list[PlanStep],
        target: PlanStep,
        message: str,
        now: float,
    ) -> str:
        remediation_id = f"{target.step_id}-remediation"
        if any(item.step_id == remediation_id for item in steps):
            remediation_id = f"{remediation_id}-{len(steps)}"

        # The failed step is skipped so its dependents can still run, and the
        # remediation step takes over responsibility for the outcome.
        target.mark_skipped(
            f"Replaced by remediation step after failure: {message}", now
        )

        remediation = PlanStep(
            step_id=remediation_id,
            title=f"Remediate failure in {target.title}",
            capability="debugging",
            description=(
                "Investigate and recover from the failure of step "
                f"{target.step_id}: {message}"
            ),
            required_tools=tuple(target.required_tools),
            depends_on=tuple(target.depends_on),
            priority=max(0, target.priority - 1),
            parameters=dict(target.parameters),
            timeout_seconds=target.timeout_seconds,
            max_attempts=max(1, target.max_attempts),
            complexity=min(target.complexity + 1, 5),
            rationale="Added by bounded re-planning after a step failure.",
        )

        # Preserve the dependency semantics for downstream steps.
        for item in steps:
            if target.step_id in item.depends_on:
                item.depends_on = tuple(
                    remediation_id if dependency == target.step_id else dependency
                    for dependency in item.depends_on
                )

        steps.append(remediation)
        return (
            f"Insert remediation step {remediation_id} after {target.step_id} "
            f"failed and was skipped."
        )

    # -- helpers ----------------------------------------------------------- #
    @staticmethod
    def _find(steps: list[PlanStep], step_id: str) -> PlanStep:
        for item in steps:
            if item.step_id == step_id:
                return item
        raise KeyError(f"Unknown step: {step_id}")

    @staticmethod
    def _describe_error(error: BaseException) -> str:
        if isinstance(error, Phase9Error):
            return f"{type(error).__name__}: {error.args[0] if error.args else ''}"
        return f"{type(error).__name__}"


__all__ = [
    "FailureClass",
    "ReplanDecision",
    "ReplanRecord",
    "Replanner",
]
