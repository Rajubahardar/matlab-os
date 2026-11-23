"""Tests for the intelligent planning layer.

Covers plan modelling and DAG validation, deterministic heuristic planning,
strict model-assisted planning with safe fallback, bounded re-planning, and
plan execution including every control-flow guard.
"""

from __future__ import annotations

import json
import time

import pytest

from matlab_os.phase9.audit import AuditLog, NullAuditLog
from matlab_os.phase9.config import PlanningConfig
from matlab_os.phase9.errors import (
    AgentSelectionError,
    PermissionDeniedError,
    PlanLimitError,
    PlanValidationError,
    ReplanLimitError,
    ResourceExhaustedError,
    StepExecutionError,
)
from matlab_os.phase9.planning.executor import PlanExecutor
from matlab_os.phase9.planning.model import (
    ExecutionPlan,
    ExecutionStatus,
    PlanStep,
    StepStatus,
)
from matlab_os.phase9.planning.planner import (
    ALLOWED_CAPABILITIES,
    TaskPlanner,
)
from matlab_os.phase9.planning.replanner import (
    FailureClass,
    ReplanDecision,
    Replanner,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _Profile:
    def __init__(self, agent_id: str = "agent-1") -> None:
        self.agent_id = agent_id


class _Selection:
    def __init__(self, agent_id: str = "agent-1") -> None:
        self.selected = _Profile(agent_id)
        self.candidates = []


class FakeSelector:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def select(self, required_capabilities=(), **kwargs):
        self.calls.append(dict(kwargs, required_capabilities=list(required_capabilities)))
        if self.error is not None:
            raise self.error
        return _Selection()


class _Admission:
    def __init__(self, admitted: bool, reason: str = "") -> None:
        self.admitted = admitted
        self.reason = reason
        self.task_id = ""
        self.slot = 1 if admitted else None


class FakeGovernor:
    def __init__(self, admitted: bool = True) -> None:
        self.admitted = admitted
        self.admitted_calls = 0
        self.released: list[str] = []

    def admit(self, task_id, *, requirements=None, priority=0):
        self.admitted_calls += 1
        return _Admission(self.admitted, "fake")

    def release(self, task_id, *, success=True):
        self.released.append(task_id)


class FakeProviderResponse:
    def __init__(self, text: str, model: str = "fake-model") -> None:
        self.text = text
        self.model = model
        self.provider_id = "fake"


class FakeProviders:
    def __init__(self, text: str | Exception) -> None:
        self.text = text
        self.calls = 0

    def generate(self, request, **kwargs):
        self.calls += 1
        if isinstance(self.text, Exception):
            raise self.text
        return FakeProviderResponse(self.text)


def _plan(*specs) -> ExecutionPlan:
    steps = []
    for index, spec in enumerate(specs, start=1):
        step_id = spec.get("id", f"step-{index:02d}")
        steps.append(
            PlanStep(
                step_id=step_id,
                title=spec.get("title", f"Step {index}"),
                capability=spec.get("capability", "analysis"),
                depends_on=tuple(spec.get("depends_on", ())),
                priority=spec.get("priority", 0),
                max_attempts=spec.get("max_attempts", 2),
                timeout_seconds=spec.get("timeout_seconds", 30.0),
            )
        )
    return ExecutionPlan.create("task", "goal", steps)


def _executor(selector=None, governor=None, handlers=None, config=None,
              permission_hook=None, replanner=None):
    cfg = config or PlanningConfig(use_model_assistance=False)
    audit = NullAuditLog()
    return PlanExecutor(
        cfg,
        selector=selector or FakeSelector(),
        governor=governor or FakeGovernor(),
        handlers=handlers or {},
        replanner=replanner or Replanner(cfg, audit=audit),
        permission_hook=permission_hook,
        audit=audit,
    )


# --------------------------------------------------------------------------- #
# Plan model
# --------------------------------------------------------------------------- #
def test_plan_validation_rejects_empty_plan():
    with pytest.raises(PlanValidationError):
        ExecutionPlan.create("task", "goal", []).validate()


def test_plan_validation_rejects_duplicate_step_ids():
    plan = _plan({"id": "dup"}, {"id": "dup"})
    with pytest.raises(PlanValidationError):
        plan.validate()


def test_plan_validation_rejects_unknown_dependency():
    plan = _plan({"id": "a"}, {"id": "b", "depends_on": ["missing"]})
    with pytest.raises(PlanValidationError):
        plan.validate()


def test_plan_validation_rejects_cycle():
    plan = _plan(
        {"id": "a", "depends_on": ["b"]},
        {"id": "b", "depends_on": ["a"]},
    )
    with pytest.raises(PlanValidationError):
        plan.validate()


def test_plan_validation_rejects_self_dependency():
    with pytest.raises(PlanValidationError):
        PlanStep(
            step_id="a", title="A", capability="analysis", depends_on=("a",)
        ).validate()


def test_plan_step_count_limit():
    plan = _plan(*[{"id": f"s{index}"} for index in range(5)])
    with pytest.raises(PlanLimitError):
        plan.validate(max_steps=3)


def test_topological_order_is_dependency_safe():
    plan = _plan(
        {"id": "a"},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["b"]},
    )
    order = [step.step_id for step in plan.topological_order()]
    assert order == ["a", "b", "c"]


def test_topological_order_prefers_priority():
    plan = _plan(
        {"id": "low", "priority": 1},
        {"id": "high", "priority": 9},
    )
    assert [step.step_id for step in plan.topological_order()] == ["high", "low"]


def test_ready_steps_only_returns_unblocked():
    plan = _plan(
        {"id": "a"},
        {"id": "b", "depends_on": ["a"]},
    )
    assert [step.step_id for step in plan.ready_steps()] == ["a"]
    plan.steps[0].mark_completed({"ok": True}, time.time())
    assert [step.step_id for step in plan.ready_steps()] == ["b"]


def test_progress_reports_counts():
    plan = _plan({"id": "a"}, {"id": "b"})
    plan.steps[0].mark_completed(None, time.time())
    progress = plan.progress()
    assert progress["total_steps"] == 2
    assert progress["status_counts"]["completed"] == 1
    assert progress["percent_complete"] == 50.0


def test_plan_round_trip_serialisation():
    plan = _plan({"id": "a"}, {"id": "b", "depends_on": ["a"]})
    restored = ExecutionPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert [step.step_id for step in restored.steps] == ["a", "b"]


# --------------------------------------------------------------------------- #
# Heuristic planning
# --------------------------------------------------------------------------- #
@pytest.fixture()
def planner() -> TaskPlanner:
    return TaskPlanner(
        PlanningConfig(use_model_assistance=False), audit=NullAuditLog()
    )


REPRESENTATIVE_TASKS = [
    "Analyse the battery model and fix the regression, then document it",
    "Create a Simulink model for a DC motor and optimise performance",
    "Summarise the repository structure",
    "Test the gateway and verify the permission boundary",
    "Research the DeepSeek API and design a provider adapter",
]


@pytest.mark.parametrize("task", REPRESENTATIVE_TASKS)
def test_heuristic_plan_is_valid_for_representative_tasks(planner, task):
    plan = planner.plan(task)
    plan.validate(max_steps=200)
    assert plan.steps
    assert plan.source == "heuristic"


@pytest.mark.parametrize("task", REPRESENTATIVE_TASKS)
def test_heuristic_plan_has_mandatory_head_and_tail(planner, task):
    plan = planner.plan(task)
    capabilities = [step.capability for step in plan.steps]
    # ``analysis`` is mandatory; ``research`` may legitimately precede it.
    assert capabilities[0] in ("research", "analysis")
    assert "analysis" in capabilities
    assert capabilities[-1] == "documentation"
    assert "verification" in capabilities


@pytest.mark.parametrize("task", REPRESENTATIVE_TASKS)
def test_heuristic_capabilities_are_in_canonical_order(planner, task):
    from matlab_os.phase9.planning.planner import PIPELINE_ORDER

    plan = planner.plan(task)
    indices = [PIPELINE_ORDER.index(step.capability) for step in plan.steps]
    assert indices == sorted(indices)


def test_heuristic_plan_is_deterministic(planner):
    first = planner.plan("Implement and test a parser")
    second = planner.plan("Implement and test a parser")
    assert [step.capability for step in first.steps] == [
        step.capability for step in second.steps
    ]
    assert [step.depends_on for step in first.steps] == [
        step.depends_on for step in second.steps
    ]


def test_heuristic_dependencies_form_a_chain(planner):
    plan = planner.plan("Implement a parser")
    assert plan.steps[0].depends_on == ()
    for previous, current in zip(plan.steps, plan.steps[1:]):
        assert previous.step_id in current.depends_on


def test_plan_respects_max_steps():
    planner = TaskPlanner(
        PlanningConfig(max_steps=2, use_model_assistance=False)
    )
    plan = planner.plan("Analyse implement document test optimise")
    assert len(plan.steps) <= 2


def test_unknown_required_capability_is_rejected(planner):
    with pytest.raises(PlanValidationError):
        planner.plan("Do something", required_capabilities=["teleport"])


def test_required_capability_is_included(planner):
    plan = planner.plan("Do something", required_capabilities=["simulation"])
    assert "simulation" in [step.capability for step in plan.steps]


def test_explain_renders_steps(planner):
    plan = planner.plan("Implement a parser and test it")
    text = planner.explain(plan)
    assert "Plan " in text
    assert "step-01" in text
    assert "depends_on" in text


def test_capability_vocabulary_is_stable():
    assert "analysis" in ALLOWED_CAPABILITIES
    assert "documentation" in ALLOWED_CAPABILITIES


# --------------------------------------------------------------------------- #
# Model-assisted planning
# --------------------------------------------------------------------------- #
def _model_payload(capability: str = "analysis", steps: int = 2) -> str:
    payload = {
        "goal": "Ship the feature",
        "rationale": "Because it is small",
        "steps": [
            {
                "step_id": f"m-{index:02d}",
                "title": f"Model step {index}",
                "capability": capability,
                "description": "model proposed",
                "depends_on": [] if index == 1 else [f"m-{index - 1:02d}"],
                "required_tools": ["workspace"],
                "complexity": 2,
            }
            for index in range(1, steps + 1)
        ],
    }
    return json.dumps(payload)


def test_model_assisted_plan_is_accepted_when_valid():
    providers = FakeProviders(_model_payload())
    planner = TaskPlanner(
        PlanningConfig(use_model_assistance=True),
        providers=providers,
        audit=NullAuditLog(),
    )
    plan = planner.plan("Implement something", use_model=True)
    assert plan.source == "model"
    assert plan.goal == "Ship the feature"
    assert providers.calls == 1
    plan.validate(max_steps=200)


def test_invalid_json_falls_back_to_heuristic():
    providers = FakeProviders("this is not json")
    planner = TaskPlanner(
        PlanningConfig(use_model_assistance=True),
        providers=providers,
        audit=NullAuditLog(),
    )
    plan = planner.plan("Implement something", use_model=True)
    assert plan.source == "heuristic"
    assert plan.metadata["model_fallback"] is True


def test_unknown_capability_from_model_is_rejected():
    providers = FakeProviders(_model_payload(capability="root_access"))
    planner = TaskPlanner(
        PlanningConfig(use_model_assistance=True),
        providers=providers,
        audit=NullAuditLog(),
    )
    plan = planner.plan("Implement something", use_model=True)
    assert plan.source == "heuristic"
    assert "root_access" not in [step.capability for step in plan.steps]


def test_model_plan_over_step_limit_falls_back():
    providers = FakeProviders(_model_payload(steps=8))
    planner = TaskPlanner(
        PlanningConfig(use_model_assistance=True, max_steps=4),
        providers=providers,
        audit=NullAuditLog(),
    )
    plan = planner.plan("Implement something", use_model=True)
    assert plan.source == "heuristic"


def test_provider_error_falls_back(monkeypatch):
    from matlab_os.phase9.errors import ProviderUnavailableError

    providers = FakeProviders(ProviderUnavailableError("down"))
    planner = TaskPlanner(
        PlanningConfig(use_model_assistance=True),
        providers=providers,
        audit=NullAuditLog(),
    )
    plan = planner.plan("Implement something", use_model=True)
    assert plan.source == "heuristic"


def test_model_plan_with_dangling_dependency_is_rejected():
    payload = {
        "goal": "g",
        "steps": [
            {
                "step_id": "m-01",
                "title": "x",
                "capability": "analysis",
                "depends_on": ["does-not-exist"],
            }
        ],
    }
    planner = TaskPlanner(
        PlanningConfig(use_model_assistance=True),
        providers=FakeProviders(json.dumps(payload)),
        audit=NullAuditLog(),
    )
    assert planner.plan("Do it", use_model=True).source == "heuristic"


# --------------------------------------------------------------------------- #
# Re-planning
# --------------------------------------------------------------------------- #
@pytest.fixture()
def replanner() -> Replanner:
    return Replanner(PlanningConfig(replan_limit=2), audit=NullAuditLog())


def test_classify_permission_failure(replanner):
    assert replanner.classify(PermissionDeniedError("no")) is (
        FailureClass.PERMISSION
    )


def test_classify_transient_failure(replanner):
    assert replanner.classify(StepExecutionError("boom")) is (
        FailureClass.TRANSIENT
    )


def test_classify_resource_failure(replanner):
    assert replanner.classify(ResourceExhaustedError("no ram")) is (
        FailureClass.RESOURCE
    )


def test_classify_timeout_failure(replanner):
    assert replanner.classify(TimeoutError("slow")) is FailureClass.TIMEOUT


def test_classify_unknown_is_permanent(replanner):
    assert replanner.classify(KeyError("x")) is FailureClass.PERMANENT


def test_permission_failure_aborts(replanner):
    plan = _plan({"id": "a"})
    assert replanner.decide(
        plan, plan.steps[0], PermissionDeniedError("no")
    ) is ReplanDecision.ABORT


def test_transient_failure_retries_while_attempts_remain(replanner):
    plan = _plan({"id": "a", "max_attempts": 3})
    plan.steps[0].attempts = 1
    assert replanner.decide(
        plan, plan.steps[0], StepExecutionError("boom")
    ) is ReplanDecision.RETRY


def test_transient_failure_reroutes_after_attempts_exhausted(replanner):
    plan = _plan({"id": "a", "max_attempts": 1})
    step = plan.steps[0]
    step.attempts = 1
    step.assigned_agent = "agent-1"
    assert replanner.decide(plan, step, StepExecutionError("boom")) is (
        ReplanDecision.REROUTE
    )


def test_revise_retry_keeps_attempts(replanner):
    plan = _plan({"id": "a"})
    step = plan.steps[0]
    step.attempts = 1
    revised = replanner.revise(
        plan, step, ReplanDecision.RETRY, StepExecutionError("boom")
    )
    assert revised.steps[0].status is StepStatus.PENDING
    assert revised.steps[0].attempts == 1
    assert revised.revision == 1
    assert revised.parent_plan_id == plan.plan_id


def test_revise_reroute_excludes_previous_agent(replanner):
    plan = _plan({"id": "a"})
    step = plan.steps[0]
    step.assigned_agent = "agent-1"
    revised = replanner.revise(
        plan, step, ReplanDecision.REROUTE, StepExecutionError("boom")
    )
    assert "agent-1" in revised.steps[0].excluded_agents


def test_revise_extend_adds_remediation_step(replanner):
    plan = _plan({"id": "a"}, {"id": "b", "depends_on": ["a"]})
    failed = plan.steps[0]
    revised = replanner.revise(
        plan, failed, ReplanDecision.EXTEND, StepExecutionError("boom")
    )
    assert len(revised.steps) == 3
    remediation = revised.steps[-1]
    assert remediation.step_id.endswith("remediation")
    assert revised.steps[0].status is StepStatus.SKIPPED
    assert remediation.step_id in revised.steps[1].depends_on


def test_revise_abort_skips_remaining(replanner):
    plan = _plan({"id": "a"}, {"id": "b", "depends_on": ["a"]})
    revised = replanner.revise(
        plan, plan.steps[0], ReplanDecision.ABORT,
        PermissionDeniedError("denied")
    )
    assert revised.steps[0].status is StepStatus.FAILED
    assert revised.steps[1].status is StepStatus.SKIPPED


def test_replan_limit_is_enforced():
    replanner = Replanner(PlanningConfig(replan_limit=0), audit=NullAuditLog())
    plan = _plan({"id": "a"})
    with pytest.raises(ReplanLimitError):
        replanner.revise(
            plan, plan.steps[0], ReplanDecision.RETRY,
            StepExecutionError("boom")
        )


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def test_executor_completes_simple_plan():
    plan = _plan({"id": "a", "capability": "analysis"})
    handlers = {"analysis": lambda step, ctx: {"ok": True}}
    executor = _executor(handlers=handlers)
    result = executor.run(plan)
    assert result.status == ExecutionStatus.COMPLETED.value
    assert result.completed == ["a"]
    assert plan.steps[0].result == {"ok": True}


def test_executor_runs_steps_in_dependency_order():
    calls: list[str] = []
    plan = _plan(
        {"id": "a", "capability": "analysis"},
        {"id": "b", "capability": "implementation", "depends_on": ["a"]},
    )
    handlers = {
        "analysis": lambda step, ctx: calls.append("a"),
        "implementation": lambda step, ctx: calls.append("b"),
    }
    _executor(handlers=handlers).run(plan)
    assert calls == ["a", "b"]


def test_executor_fails_when_handler_missing():
    plan = _plan({"id": "a", "capability": "simulation"})
    result = _executor(handlers={}).run(plan)
    assert result.status == ExecutionStatus.FAILED.value
    assert "a" in result.failed


def test_executor_aborts_when_selection_fails():
    plan = _plan({"id": "a", "capability": "analysis"})
    selector = FakeSelector(AgentSelectionError("nobody"))
    result = _executor(selector=selector, handlers={
        "analysis": lambda step, ctx: None
    }).run(plan)
    assert result.status == ExecutionStatus.FAILED.value


def test_executor_surfaces_approval_requirement():
    plan = _plan({"id": "a", "capability": "implementation"})

    def deny(step, agent_id):
        raise PermissionDeniedError("approval missing")

    result = _executor(
        handlers={"implementation": lambda step, ctx: None},
        permission_hook=deny,
    ).run(plan)
    assert result.status == ExecutionStatus.AWAITING_APPROVAL.value
    assert result.awaiting_approval is True


def test_executor_retries_transient_failure():
    attempts = {"n": 0}

    def flaky(step, ctx):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise StepExecutionError("first attempt fails")
        return {"ok": True}

    plan = _plan({"id": "a", "capability": "analysis", "max_attempts": 2})
    result = _executor(handlers={"analysis": flaky}).run(plan)
    assert result.status == ExecutionStatus.COMPLETED.value
    assert attempts["n"] == 2
    assert result.replans == 1


def test_executor_aborts_when_governor_refuses():
    plan = _plan({"id": "a", "capability": "analysis", "max_attempts": 1})
    governor = FakeGovernor(admitted=False)
    result = _executor(governor=governor, handlers={
        "analysis": lambda step, ctx: None
    }).run(plan)
    assert result.status == ExecutionStatus.FAILED.value
    assert governor.admitted_calls >= 1


def test_executor_marks_blocked_dependents_skipped():
    from matlab_os.phase9.errors import ProviderAuthError

    plan = _plan(
        {"id": "a", "capability": "analysis", "max_attempts": 1},
        {"id": "b", "capability": "implementation", "depends_on": ["a"]},
    )

    def failing(step, ctx):
        raise ProviderAuthError("permanent failure")

    result = _executor(handlers={
        "analysis": failing,
        "implementation": lambda step, ctx: None,
    }).run(plan)
    assert result.status == ExecutionStatus.FAILED.value
    assert "b" in result.skipped


def test_permission_failure_during_execution_pauses_for_approval():
    plan = _plan(
        {"id": "a", "capability": "analysis", "max_attempts": 1},
        {"id": "b", "capability": "implementation", "depends_on": ["a"]},
    )

    def failing(step, ctx):
        raise PermissionDeniedError("denied")

    result = _executor(handlers={
        "analysis": failing,
        "implementation": lambda step, ctx: None,
    }).run(plan)
    assert result.status == ExecutionStatus.AWAITING_APPROVAL.value
    assert "b" not in result.completed


def test_executor_emits_events():
    plan = _plan({"id": "a", "capability": "analysis"})
    events: list[dict] = []
    result = _executor(
        handlers={"analysis": lambda step, ctx: 1}
    ).run(plan, on_event=events.append)
    names = [event["event"] for event in events]
    assert "step.completed" in names
    assert "plan.finished" in names
    assert result.events


def test_executor_reports_wrapped_handler_exception():
    plan = _plan({"id": "a", "capability": "analysis", "max_attempts": 1})

    def boom(step, ctx):
        raise ValueError("unexpected")

    result = _executor(handlers={"analysis": boom}).run(plan)
    assert result.status == ExecutionStatus.FAILED.value
    assert "StepExecutionError" in (plan.steps[0].error or "")


def test_executor_passes_deadline_and_plan_context():
    captured: dict = {}

    def capture(step, ctx):
        captured.update(ctx)

    plan = _plan({"id": "a", "capability": "analysis"})
    _executor(handlers={"analysis": capture}).run(plan, context={"shared": {}})
    assert "deadline_monotonic" in captured
    assert captured["step_id"] == "a"
    assert captured["capability"] == "analysis"


def test_executor_plan_result_serialises():
    plan = _plan({"id": "a", "capability": "analysis"})
    result = _executor(
        handlers={"analysis": lambda step, ctx: 1}
    ).run(plan)
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["status"] == ExecutionStatus.COMPLETED.value
