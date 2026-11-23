"""Tests for the Phase 9 agent-selection and collaboration layers.

Covered contract sections: ``docs/ARCHITECTURE_PLAN_PHASE9.md`` 4.10 and 4.11.

The suite is deliberately deterministic: agent/dependency free, no network, no
wall-clock sleeps, and an injected clock everywhere time matters.
"""

from __future__ import annotations

import json
import threading

import pytest

from matlab_os.phase9.agents.capability import (
    AgentCapability,
    AgentProfile,
    AgentStatus,
)
from matlab_os.phase9.agents.directory import AgentDirectory
from matlab_os.phase9.agents.selector import AgentSelector
from matlab_os.phase9.collaboration.coordinator import (
    AGGREGATION_COMPLETED,
    AGGREGATION_CONFLICT,
    AGGREGATION_FAILED,
    AGGREGATION_PARTIAL,
    DELEGATION_ACCEPTED,
    DELEGATION_COMPLETED,
    DELEGATION_FAILED,
    DELEGATION_PENDING,
    DELEGATION_TIMEOUT,
    AggregationResult,
    CollaborationCoordinator,
    DelegationRecord,
)
from matlab_os.phase9.collaboration.protocol import AgentMessage, MessageKind
from matlab_os.phase9.config import CollaborationConfig
from matlab_os.phase9.errors import (
    AgentNotFoundError,
    AgentSelectionError,
    CollaborationError,
    CollaborationTimeoutError,
    ConfigurationError,
    DelegationCycleError,
    DelegationDepthExceededError,
    DelegationLimitError,
)


# --------------------------------------------------------------------------- #
# Fixtures and builders
# --------------------------------------------------------------------------- #
def make_capability(
    name: str = "code",
    tools: tuple[str, ...] = (),
    max_complexity: int = 3,
) -> AgentCapability:
    return AgentCapability(
        name=name, tools=tools, max_complexity=max_complexity
    )


def make_profile(agent_id: str, **overrides) -> AgentProfile:
    options: dict = {
        "display_name": agent_id,
        "capabilities": {"code": make_capability()},
        "tools": frozenset(),
        "permission_scope": frozenset(),
        "max_complexity": 3,
        "max_concurrent_tasks": 1,
        "locality": "local",
        "status": AgentStatus.AVAILABLE,
        "enabled": True,
        "cost_weight": 1.0,
    }
    options.update(overrides)
    return AgentProfile(agent_id=agent_id, **options)


def make_message(**overrides) -> AgentMessage:
    fields: dict = {
        "message_id": "message-1",
        "kind": MessageKind.DELEGATE,
        "sender": "agent-a",
        "recipient": "agent-b",
        "task_id": "task-1",
        "correlation_id": "corr-1",
        "depth": 0,
        "payload": {"instruction": "review the module"},
        "created_at": 1000.0,
    }
    fields.update(overrides)
    return AgentMessage(**fields)


def make_config(**overrides) -> CollaborationConfig:
    defaults: dict = {
        "max_delegation_depth": 2,
        "max_agents_per_task": 4,
        "max_total_delegations": 12,
        "max_messages": 64,
        "max_payload_bytes": 64_000,
        "message_timeout_seconds": 60.0,
        "detect_conflicts": True,
    }
    defaults.update(overrides)
    return CollaborationConfig(**defaults)


class FakeClock:
    """Deterministic clock injected into the coordinator."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingAudit:
    """Audit double that records the calls made by the coordinator."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record(
        self,
        action,
        actor,
        outcome,
        details=None,
        correlation_id=None,
    ) -> None:
        self.entries.append(
            {
                "action": action,
                "actor": actor,
                "outcome": outcome,
                "details": details or {},
                "correlation_id": correlation_id,
            }
        )

    def actions(self) -> list[str]:
        return [str(entry["action"]) for entry in self.entries]

    def outcomes(self) -> list[str]:
        return [str(entry["outcome"]) for entry in self.entries]


def build_coordinator(config=None, *, clock=None, audit=None):
    clock = clock if clock is not None else FakeClock()
    audit = audit if audit is not None else RecordingAudit()
    coordinator = CollaborationCoordinator(
        config if config is not None else make_config(),
        audit=audit,
        clock=clock,
    )
    return coordinator, clock, audit


# --------------------------------------------------------------------------- #
# Agent capability and profile model
# --------------------------------------------------------------------------- #
def test_capability_round_trip_and_validation():
    capability = AgentCapability(
        name="code",
        tools=["pytest", "coverage"],
        max_complexity=2,
        description="write and verify code",
    )
    assert capability.tools == ("pytest", "coverage")
    assert capability.supports_complexity(2)
    assert not capability.supports_complexity(3)
    assert AgentCapability.from_dict(capability.to_dict()) == capability
    with pytest.raises(ValueError):
        AgentCapability(name="")
    with pytest.raises(ValueError):
        AgentCapability(name="code", max_complexity=0)
    with pytest.raises(ValueError):
        AgentCapability(name="code", tools="pytest")


def test_profile_round_trip_through_dict():
    profile = make_profile(
        "agent-code",
        tools={"pytest"},
        permission_scope={"workspace.read"},
        metadata={"role": "tester"},
        cost_weight=1.5,
    )
    restored = AgentProfile.from_dict(profile.to_dict())
    assert restored == profile
    assert restored.to_dict() == profile.to_dict()


def test_profile_from_dict_requires_agent_id():
    with pytest.raises(ValueError):
        AgentProfile.from_dict({"display_name": "x"})


@pytest.mark.parametrize(
    "overrides",
    [
        {"locality": "moon"},
        {"cost_weight": 0.0},
        {"cost_weight": -1.0},
        {"max_complexity": 0},
        {"max_concurrent_tasks": 0},
        {"status": "sleeping"},
    ],
)
def test_profile_validation_rejects_invalid_values(overrides):
    with pytest.raises(ValueError):
        make_profile("agent-code", **overrides)


def test_profile_rejects_blank_agent_id():
    with pytest.raises(ValueError):
        make_profile("   ")


def test_profile_rejects_capability_key_mismatch():
    with pytest.raises(ValueError):
        make_profile(
            "agent-code", capabilities={"code": make_capability(name="other")}
        )


# --------------------------------------------------------------------------- #
# Agent directory
# --------------------------------------------------------------------------- #
def test_directory_register_and_get_returns_copies():
    directory = AgentDirectory()
    directory.register(make_profile("agent-code", metadata={"role": "tester"}))
    first = directory.get("agent-code")
    assert first.agent_id == "agent-code"
    first.metadata["role"] = "mutated"
    assert directory.get("agent-code").metadata["role"] == "tester"
    assert [profile.agent_id for profile in directory.all()] == ["agent-code"]
    assert "agent-code" in directory
    assert len(directory) == 1


def test_directory_duplicate_registration_raises_value_error():
    directory = AgentDirectory()
    directory.register(make_profile("dup"))
    with pytest.raises(ValueError):
        directory.register(make_profile("dup"))
    directory.unregister("dup")
    directory.register(make_profile("dup"))


def test_directory_unknown_agent_raises_agent_not_found():
    directory = AgentDirectory()
    with pytest.raises(AgentNotFoundError):
        directory.get("missing")
    with pytest.raises(AgentNotFoundError):
        directory.unregister("missing")
    with pytest.raises(AgentNotFoundError):
        directory.set_status("missing", AgentStatus.BUSY)
    with pytest.raises(AgentNotFoundError):
        directory.mark_busy("missing")
    with pytest.raises(AgentNotFoundError):
        directory.active_count("missing")


def test_directory_active_count_never_goes_negative():
    directory = AgentDirectory()
    directory.register(make_profile("agent-code"))
    for _ in range(3):
        directory.mark_available("agent-code")
    assert directory.active_count("agent-code") == 0
    directory.mark_busy("agent-code")
    directory.mark_busy("agent-code")
    assert directory.active_count("agent-code") == 2
    for _ in range(5):
        directory.mark_available("agent-code")
    assert directory.active_count("agent-code") == 0


def test_directory_busy_and_available_status_transitions():
    directory = AgentDirectory()
    directory.register(make_profile("worker"))
    directory.mark_busy("worker")
    assert directory.get("worker").status is AgentStatus.BUSY
    directory.mark_available("worker")
    assert directory.get("worker").status is AgentStatus.AVAILABLE
    directory.set_status("worker", AgentStatus.DEGRADED)
    assert directory.get("worker").status is AgentStatus.DEGRADED
    with pytest.raises(ValueError):
        directory.set_status("worker", "sleeping")


def test_directory_snapshot_is_deterministic_and_complete():
    directory = AgentDirectory()
    directory.register(make_profile("beta", max_concurrent_tasks=2))
    directory.register(make_profile("alpha", tools={"pytest"}))
    directory.mark_busy("alpha")
    snapshot = directory.snapshot()
    assert [entry["agent_id"] for entry in snapshot] == ["alpha", "beta"]
    alpha = snapshot[0]
    assert alpha["active_count"] == 1
    assert alpha["status"] == "busy"
    assert alpha["tools"] == ["pytest"]
    assert alpha["capabilities"] == ["code"]
    assert alpha["max_concurrent_tasks"] == 1
    assert alpha["metadata"] == {}


# --------------------------------------------------------------------------- #
# Selector: happy path and explanation
# --------------------------------------------------------------------------- #
def test_selector_happy_path_selects_eligible_agent():
    directory = AgentDirectory()
    directory.register(make_profile("agent-code"))
    selector = AgentSelector(directory)
    result = selector.select(required_capabilities=["code"], complexity=2)
    assert result.selected.agent_id == "agent-code"
    assert result.selected_id == "agent-code"
    assert result.explanation
    assert result.candidates[0].agent_id == "agent-code"
    assert result.candidates[0].rejected_reason is None
    assert result.score_breakdown["agent-code"]["total"] > 0


def test_selector_records_rejection_reasons_for_ineligible_agents():
    directory = AgentDirectory()
    directory.register(make_profile("usable"))
    directory.register(make_profile("broken", enabled=False))
    result = AgentSelector(directory).select(required_capabilities=["code"])
    assert result.selected.agent_id == "usable"
    rejected = {
        candidate.agent_id: candidate.rejected_reason
        for candidate in result.rejected_candidates()
    }
    assert rejected == {"broken": "status:disabled"}
    assert "Rejected 1 candidate(s)" in result.explanation


def test_selector_explanation_lists_score_components():
    directory = AgentDirectory()
    directory.register(make_profile("agent-code"))
    result = AgentSelector(directory).select(required_capabilities=["code"])
    breakdown = result.score_breakdown["agent-code"]
    assert set(breakdown) == {
        "capability",
        "complexity_fit",
        "capacity",
        "locality",
        "cost",
        "total",
    }
    assert "Selected agent 'agent-code'" in result.explanation


# --------------------------------------------------------------------------- #
# Selector: every hard filter records a machine-readable reason
# --------------------------------------------------------------------------- #
def test_selector_rejects_disabled_status():
    directory = AgentDirectory()
    directory.register(make_profile("off", status=AgentStatus.DISABLED))
    with pytest.raises(AgentSelectionError) as excinfo:
        AgentSelector(directory).select(required_capabilities=["code"])
    assert "status:disabled" in str(excinfo.value)


def test_selector_rejects_disabled_flag():
    directory = AgentDirectory()
    directory.register(make_profile("off", enabled=False))
    with pytest.raises(AgentSelectionError) as excinfo:
        AgentSelector(directory).select(required_capabilities=["code"])
    assert "status:disabled" in str(excinfo.value)


def test_selector_rejects_unavailable_agent():
    directory = AgentDirectory()
    directory.register(make_profile("gone", status=AgentStatus.UNAVAILABLE))
    with pytest.raises(AgentSelectionError) as excinfo:
        AgentSelector(directory).select(required_capabilities=["code"])
    assert "status:unavailable" in str(excinfo.value)


def test_selector_rejects_missing_capability():
    directory = AgentDirectory()
    directory.register(make_profile("no-code", capabilities={}))
    with pytest.raises(AgentSelectionError) as excinfo:
        AgentSelector(directory).select(required_capabilities=["code"])
    assert "capability_missing:code" in str(excinfo.value)


def test_selector_rejects_missing_tool():
    directory = AgentDirectory()
    directory.register(make_profile("no-tools"))
    with pytest.raises(AgentSelectionError) as excinfo:
        AgentSelector(directory).select(required_tools=["pytest"])
    assert "tool_missing:pytest" in str(excinfo.value)


def test_selector_rejects_insufficient_complexity():
    directory = AgentDirectory()
    directory.register(
        make_profile(
            "small",
            max_complexity=2,
            capabilities={"code": make_capability(max_complexity=2)},
        )
    )
    with pytest.raises(AgentSelectionError) as excinfo:
        AgentSelector(directory).select(
            required_capabilities=["code"], complexity=3
        )
    assert "complexity:3>2" in str(excinfo.value)


def test_selector_rejects_missing_permission_boundary():
    directory = AgentDirectory()
    directory.register(make_profile("reader"))
    with pytest.raises(AgentSelectionError) as excinfo:
        AgentSelector(directory).select(
            required_permissions=["workspace.write"]
        )
    assert "permission_missing:workspace.write" in str(excinfo.value)


def test_selector_allows_agent_with_granted_permission():
    directory = AgentDirectory()
    directory.register(
        make_profile("writer", permission_scope={"workspace.write"})
    )
    result = AgentSelector(directory).select(
        required_permissions=["workspace.write"]
    )
    assert result.selected.agent_id == "writer"


def test_selector_rejects_agent_at_capacity():
    directory = AgentDirectory()
    directory.register(make_profile("full", max_concurrent_tasks=1))
    directory.mark_busy("full")
    with pytest.raises(AgentSelectionError) as excinfo:
        AgentSelector(directory).select(required_capabilities=["code"])
    assert "capacity:1/1" in str(excinfo.value)


def test_selector_rejects_excluded_agent():
    directory = AgentDirectory()
    directory.register(make_profile("only"))
    with pytest.raises(AgentSelectionError) as excinfo:
        AgentSelector(directory).select(exclude_agents=["only"])
    assert "excluded" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Selector: empty/invalid requests and no silent fallback
# --------------------------------------------------------------------------- #
def test_selector_empty_directory_raises_agent_selection_error():
    with pytest.raises(AgentSelectionError) as excinfo:
        AgentSelector(AgentDirectory()).select()
    assert "No agents" in str(excinfo.value)


def test_selector_error_lists_every_rejection_reason():
    directory = AgentDirectory()
    directory.register(make_profile("off", enabled=False))
    directory.register(make_profile("gone", status=AgentStatus.UNAVAILABLE))
    with pytest.raises(AgentSelectionError) as excinfo:
        AgentSelector(directory).select(required_capabilities=["code"])
    message = str(excinfo.value)
    assert "status:disabled" in message
    assert "status:unavailable" in message
    assert "rejected 2 candidate" in message


def test_selector_rejects_invalid_requirements():
    directory = AgentDirectory()
    directory.register(make_profile("only"))
    selector = AgentSelector(directory)
    with pytest.raises(ValueError):
        selector.select(complexity=0)
    with pytest.raises(ValueError):
        selector.select(required_capabilities="code")
    with pytest.raises(ValueError):
        selector.select(required_tools=[1])


# --------------------------------------------------------------------------- #
# Selector: deterministic ranking
# --------------------------------------------------------------------------- #
def test_selector_tie_break_is_ascending_agent_id():
    directory = AgentDirectory()
    directory.register(make_profile("beta"))
    directory.register(make_profile("alpha"))
    result = AgentSelector(directory).select()
    assert result.selected.agent_id == "alpha"
    assert [candidate.agent_id for candidate in result.candidates] == [
        "alpha",
        "beta",
    ]


def test_selector_prefers_local_when_requested():
    directory = AgentDirectory()
    directory.register(make_profile("aaa_remote", locality="remote"))
    directory.register(make_profile("zzz_local", locality="local"))
    selector = AgentSelector(directory)
    assert selector.select(prefer_local=True).selected.agent_id == "zzz_local"
    assert selector.select(prefer_local=False).selected.agent_id == "aaa_remote"


def test_selector_prefers_more_specific_capability():
    directory = AgentDirectory()
    directory.register(
        make_profile("agent-basic", capabilities={"code": make_capability()})
    )
    directory.register(
        make_profile(
            "agent-rich",
            capabilities={
                "code": make_capability(
                    tools=("pytest", "coverage", "lint", "mypy")
                )
            },
        )
    )
    result = AgentSelector(directory).select(required_capabilities=["code"])
    assert result.selected.agent_id == "agent-rich"


def test_selector_prefers_lower_cost_weight():
    directory = AgentDirectory()
    directory.register(make_profile("aaa_expensive", cost_weight=2.0))
    directory.register(make_profile("zzz_cheap", cost_weight=0.5))
    result = AgentSelector(directory).select()
    assert result.selected.agent_id == "zzz_cheap"


def test_selector_prefers_better_complexity_fit():
    directory = AgentDirectory()
    directory.register(
        make_profile(
            "aaa_roomy",
            max_complexity=5,
            capabilities={"code": make_capability(max_complexity=5)},
        )
    )
    directory.register(
        make_profile(
            "zzz_tight",
            max_complexity=3,
            capabilities={"code": make_capability(max_complexity=3)},
        )
    )
    result = AgentSelector(directory).select(
        required_capabilities=["code"], complexity=3
    )
    assert result.selected.agent_id == "zzz_tight"


def test_selector_keeps_busy_and_degraded_agents_selectable():
    directory = AgentDirectory()
    directory.register(
        make_profile(
            "busy", status=AgentStatus.BUSY, max_concurrent_tasks=2
        )
    )
    directory.register(
        make_profile("degraded", status=AgentStatus.DEGRADED)
    )
    result = AgentSelector(directory).select(required_capabilities=["code"])
    eligible = {
        candidate.agent_id
        for candidate in result.candidates
        if candidate.rejected_reason is None
    }
    assert eligible == {"busy", "degraded"}


def test_selector_allows_busy_agent_with_free_capacity():
    directory = AgentDirectory()
    directory.register(make_profile("worker", max_concurrent_tasks=2))
    directory.mark_busy("worker")
    result = AgentSelector(directory).select(required_capabilities=["code"])
    assert result.selected.agent_id == "worker"


# --------------------------------------------------------------------------- #
# Selector: optional resource-governor admission
# --------------------------------------------------------------------------- #
class DictDenyGovernor:
    """Non-mutating governor probe that always denies."""

    def __init__(self, reason: str = "insufficient_ram") -> None:
        self.reason = reason

    def can_admit(self, agent_id):
        return {"admitted": False, "reason": self.reason}


class Admission:
    """Admission result shaped like ``resources.governor.Admission``."""

    def __init__(self, admitted: bool, reason: str = "ok") -> None:
        self.admitted = admitted
        self.reason = reason


class AdmitOnlyGovernor:
    """Governor that only exposes the mutating ``admit`` call."""

    def __init__(self, admitted: bool, reason: str = "insufficient_cpu") -> None:
        self._admitted = admitted
        self._reason = reason

    def admit(self, agent_id):
        return Admission(self._admitted, self._reason)


def test_selector_governor_denial_rejects_with_reason():
    directory = AgentDirectory()
    directory.register(make_profile("only"))
    selector = AgentSelector(directory, governor=DictDenyGovernor())
    with pytest.raises(AgentSelectionError) as excinfo:
        selector.select(required_capabilities=["code"])
    assert "governor:insufficient_ram" in str(excinfo.value)


def test_selector_governor_admission_protocol():
    directory = AgentDirectory()
    directory.register(make_profile("only"))
    denied = AgentSelector(directory, governor=AdmitOnlyGovernor(False))
    with pytest.raises(AgentSelectionError) as excinfo:
        denied.select()
    assert "governor:insufficient_cpu" in str(excinfo.value)

    allowed = AgentSelector(directory, governor=AdmitOnlyGovernor(True))
    assert allowed.select().selected.agent_id == "only"


# --------------------------------------------------------------------------- #
# Protocol: validation, round-trip, redaction, digest
# --------------------------------------------------------------------------- #
def test_message_round_trip_dict_and_json():
    message = make_message(
        payload={"instruction": "review", "nested": {"items": [1, 2, 3]}}
    )
    message.validate(10_000)
    restored = AgentMessage.from_dict(message.to_dict())
    assert restored == message
    assert AgentMessage.from_json(message.to_json()) == message


def test_message_to_dict_redacts_secret_payload():
    message = make_message(
        payload={
            "api_key": "sk-abcdefghijklmnop",
            "header": "Bearer abcdefghijklmnop",
        }
    )
    message.validate(10_000)
    data = message.to_dict()
    assert data["payload"]["api_key"] == "[REDACTED]"
    assert "sk-abcdefghijklmnop" not in json.dumps(data)
    assert "sk-abcdefghijklmnop" not in message.to_json()


def test_message_digest_is_stable_and_canonical():
    first = make_message()
    second = make_message()
    assert first.digest() == second.digest()
    assert len(first.digest()) == 64
    int(first.digest(), 16)
    changed = make_message(payload={"instruction": "different"})
    assert changed.digest() != first.digest()


def test_message_payload_size_limit_enforced():
    message = make_message(payload={"blob": "x" * 500})
    with pytest.raises(CollaborationError):
        message.validate(100)
    message.validate(10_000)
    with pytest.raises(ValueError):
        message.validate(0)


def test_message_validation_rejects_empty_identifier():
    message = make_message(sender="")
    with pytest.raises(CollaborationError):
        message.validate(1_000)


def test_message_validation_rejects_negative_depth():
    message = make_message(depth=-1)
    with pytest.raises(CollaborationError):
        message.validate(1_000)


def test_message_validation_rejects_non_dict_payload():
    message = make_message(payload=["not", "a", "dict"])
    with pytest.raises(CollaborationError):
        message.validate(1_000)


def test_message_validation_rejects_wrong_schema_version():
    message = make_message(schema_version=2)
    with pytest.raises(CollaborationError):
        message.validate(1_000)


def test_message_construction_rejects_unknown_kind():
    with pytest.raises(ValueError):
        make_message(kind="explode")


def test_message_from_dict_and_json_reject_bad_input():
    with pytest.raises(CollaborationError):
        AgentMessage.from_dict({"kind": "delegate"})
    malformed = make_message().to_dict()
    malformed["kind"] = "explode"
    with pytest.raises(CollaborationError):
        AgentMessage.from_dict(malformed)
    with pytest.raises(CollaborationError):
        AgentMessage.from_json("{not json")
    with pytest.raises(CollaborationError):
        AgentMessage.from_json("[]")


# --------------------------------------------------------------------------- #
# Coordinator: delegation lifecycle and budgets
# --------------------------------------------------------------------------- #
def test_delegate_creates_pending_record_and_audits():
    coordinator, clock, audit = build_coordinator()
    record = coordinator.delegate(
        parent="agent-a",
        child="agent-b",
        task_id="task-1",
        payload={"instruction": "review"},
    )
    assert isinstance(record, DelegationRecord)
    assert record.status == DELEGATION_PENDING
    assert record.parent == "agent-a"
    assert record.child == "agent-b"
    assert record.task_id == "task-1"
    assert record.depth == 0
    assert record.created_at == clock.now
    assert record.timeout_seconds == 60.0
    assert coordinator.inventory()["delegations"] == 1
    assert audit.actions() == ["collab.delegate"]
    assert audit.outcomes() == ["success"]


def test_delegation_depth_exceeded():
    coordinator, _, audit = build_coordinator(
        make_config(max_delegation_depth=1)
    )
    with pytest.raises(DelegationDepthExceededError):
        coordinator.delegate(
            parent="a", child="b", task_id="t", payload={}, depth=2
        )
    assert "collab.depth" in audit.actions()
    allowed = coordinator.delegate(
        parent="a", child="b", task_id="t", payload={}, depth=1
    )
    assert allowed.depth == 1


def test_delegation_cycle_detected():
    coordinator, _, audit = build_coordinator()
    coordinator.delegate(parent="a", child="b", task_id="t1", payload={})
    with pytest.raises(DelegationCycleError):
        coordinator.delegate(
            parent="b", child="a", task_id="t1", payload={}, depth=1
        )
    with pytest.raises(DelegationCycleError):
        coordinator.delegate(parent="a", child="a", task_id="t1", payload={})
    assert audit.actions().count("collab.cycle") == 2
    # The same edge is not a cycle in a different task.
    coordinator.delegate(parent="b", child="a", task_id="t2", payload={})


def test_total_delegation_limit_enforced():
    coordinator, _, audit = build_coordinator(
        make_config(max_total_delegations=1)
    )
    coordinator.delegate(parent="a", child="b", task_id="t1", payload={})
    with pytest.raises(DelegationLimitError):
        coordinator.delegate(parent="c", child="d", task_id="t2", payload={})
    assert "collab.limit" in audit.actions()
    assert coordinator.inventory()["delegations"] == 1


def test_max_agents_per_task_enforced():
    coordinator, _, audit = build_coordinator(
        make_config(max_agents_per_task=2)
    )
    coordinator.delegate(parent="a", child="b", task_id="t1", payload={})
    with pytest.raises(DelegationLimitError):
        coordinator.delegate(parent="a", child="c", task_id="t1", payload={})
    assert "collab.limit" in audit.actions()
    # The width budget is per task, not global.
    coordinator.delegate(parent="a", child="c", task_id="t2", payload={})


def test_max_messages_limit_enforced():
    coordinator, _, audit = build_coordinator(make_config(max_messages=2))
    record = coordinator.delegate(
        parent="a", child="b", task_id="t1", payload={}
    )
    coordinator.accept(record.delegation_id)
    with pytest.raises(DelegationLimitError):
        coordinator.complete(record.delegation_id, {"answer": 1})
    assert record.status == DELEGATION_ACCEPTED
    assert coordinator.inventory()["messages"] == 2
    assert "collab.limit" in audit.actions()


def test_timeout_enforced_on_accept_with_injected_clock():
    clock = FakeClock()
    coordinator, _, audit = build_coordinator(clock=clock)
    boundary = coordinator.delegate(
        parent="a", child="b", task_id="t1", payload={}, timeout_seconds=5.0
    )
    expired = coordinator.delegate(
        parent="a",
        child="b",
        task_id="t1",
        payload={},
        depth=1,
        timeout_seconds=5.0,
    )
    clock.advance(5.0)
    assert (
        coordinator.accept(boundary.delegation_id).status
        == DELEGATION_ACCEPTED
    )
    clock.advance(0.5)
    with pytest.raises(CollaborationTimeoutError):
        coordinator.accept(expired.delegation_id)
    assert expired.status == DELEGATION_TIMEOUT
    assert "collab.timeout" in audit.actions()


def test_timeout_enforced_on_complete():
    clock = FakeClock()
    coordinator, _, _ = build_coordinator(clock=clock)
    record = coordinator.delegate(
        parent="a", child="b", task_id="t1", payload={}, timeout_seconds=2.0
    )
    clock.advance(2.5)
    with pytest.raises(CollaborationTimeoutError):
        coordinator.complete(record.delegation_id, {"answer": 1})
    assert record.status == DELEGATION_TIMEOUT
    assert record.result is None


def test_complete_and_fail_transitions():
    coordinator, _, audit = build_coordinator()
    record = coordinator.delegate(
        parent="a", child="b", task_id="t1", payload={}
    )
    assert (
        coordinator.accept(record.delegation_id).status
        == DELEGATION_ACCEPTED
    )
    completed = coordinator.complete(record.delegation_id, {"answer": 42})
    assert completed.status == DELEGATION_COMPLETED
    assert completed.result == {"answer": 42}
    assert completed.finished_at is not None

    other = coordinator.delegate(
        parent="a", child="b", task_id="t2", payload={}
    )
    failed = coordinator.fail(other.delegation_id, ValueError("boom"))
    assert failed.status == DELEGATION_FAILED
    assert "ValueError" in (failed.error or "")
    assert "boom" in (failed.error or "")
    assert "collab.complete" in audit.actions()
    assert "collab.fail" in audit.actions()


def test_fail_scrubs_secret_from_error_text():
    coordinator, _, _ = build_coordinator()
    record = coordinator.delegate(
        parent="a", child="b", task_id="t1", payload={}
    )
    coordinator.fail(
        record.delegation_id, RuntimeError("token=sk-supersecret12345")
    )
    assert record.status == DELEGATION_FAILED
    assert "sk-supersecret12345" not in (record.error or "")


def test_invalid_delegation_transitions_raise_collaboration_errors():
    coordinator, _, _ = build_coordinator()
    with pytest.raises(CollaborationError):
        coordinator.accept("missing")
    with pytest.raises(CollaborationError):
        coordinator.complete("missing", 1)
    with pytest.raises(CollaborationError):
        coordinator.fail("missing", "error")

    record = coordinator.delegate(
        parent="a", child="b", task_id="t1", payload={}
    )
    coordinator.accept(record.delegation_id)
    with pytest.raises(CollaborationError):
        coordinator.accept(record.delegation_id)
    coordinator.complete(record.delegation_id, {"answer": 1})
    with pytest.raises(CollaborationError):
        coordinator.complete(record.delegation_id, {"answer": 2})
    with pytest.raises(CollaborationError):
        coordinator.fail(record.delegation_id, "late error")


def test_delegate_rejects_oversized_payload():
    coordinator, _, _ = build_coordinator(
        make_config(max_payload_bytes=256)
    )
    with pytest.raises(CollaborationError):
        coordinator.delegate(
            parent="a", child="b", task_id="t1", payload={"blob": "x" * 1000}
        )
    inventory = coordinator.inventory()
    assert inventory["delegations"] == 0
    assert inventory["messages"] == 0


def test_delegate_rejects_invalid_arguments():
    coordinator, _, _ = build_coordinator()
    with pytest.raises(ValueError):
        coordinator.delegate(parent="", child="b", task_id="t", payload={})
    with pytest.raises(ValueError):
        coordinator.delegate(
            parent="a", child="b", task_id="t", payload={}, depth=-1
        )
    with pytest.raises(ValueError):
        coordinator.delegate(parent="a", child="b", task_id="t", payload=[1])
    with pytest.raises(ValueError):
        coordinator.delegate(
            parent="a", child="b", task_id="t", payload={}, timeout_seconds=0
        )


def test_audit_records_every_state_transition():
    coordinator, _, audit = build_coordinator()
    record = coordinator.delegate(
        parent="a", child="b", task_id="t1", payload={}
    )
    coordinator.accept(record.delegation_id)
    coordinator.complete(record.delegation_id, {"answer": 1})
    assert audit.actions() == [
        "collab.delegate",
        "collab.accept",
        "collab.complete",
    ]
    assert audit.outcomes() == ["success", "success", "success"]


def test_coordinator_works_without_injected_audit():
    coordinator = CollaborationCoordinator(make_config(), clock=FakeClock())
    record = coordinator.delegate(
        parent="a", child="b", task_id="t1", payload={}
    )
    assert record.status == DELEGATION_PENDING


def test_coordinator_rejects_invalid_config():
    with pytest.raises(ConfigurationError):
        CollaborationCoordinator(
            CollaborationConfig(max_delegation_depth=9)
        )


# --------------------------------------------------------------------------- #
# Coordinator: aggregation and conflict detection
# --------------------------------------------------------------------------- #
def test_aggregate_completed_without_conflicts():
    coordinator, _, audit = build_coordinator()
    results = {
        "agent-a": {"status": "COMPLETED", "answer": 1},
        "agent-b": {"status": "COMPLETED", "notes": "ok"},
    }
    aggregation = coordinator.aggregate("task-1", results)
    assert isinstance(aggregation, AggregationResult)
    assert aggregation.status == AGGREGATION_COMPLETED
    assert aggregation.conflicts == []
    assert aggregation.results == results
    assert aggregation.explanation
    assert "collab.aggregate" in audit.actions()


def test_aggregate_conflict_reports_and_does_not_choose():
    coordinator, _, audit = build_coordinator()
    results = {
        "agent-a": {"status": "COMPLETED", "answer": {"value": 1}},
        "agent-b": {"status": "COMPLETED", "answer": {"value": 2}},
    }
    aggregation = coordinator.aggregate("task-1", results)
    assert aggregation.status == AGGREGATION_CONFLICT
    assert aggregation.conflicts == [
        {
            "key": "answer.value",
            "values": [1, 2],
            "agents": ["agent-a", "agent-b"],
        }
    ]
    # Nothing is merged and no value is chosen.
    assert aggregation.results == results
    assert "collab.conflict" in audit.actions()


def test_detect_conflicts_payload_shape():
    coordinator, _, _ = build_coordinator()
    conflicts = coordinator.detect_conflicts(
        {
            "agent-a": {"x": {"y": 1}, "same": 5, "only_a": 1},
            "agent-b": {"x": {"y": 2}, "same": 5},
            "agent-c": {"x": {"y": 1}},
        }
    )
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert set(conflict) == {"key", "values", "agents"}
    assert conflict["key"] == "x.y"
    assert conflict["values"] == [1, 2]
    assert conflict["agents"] == ["agent-a", "agent-b", "agent-c"]


def test_detect_conflicts_ignores_equal_values_and_absent_keys():
    coordinator, _, _ = build_coordinator()
    assert coordinator.detect_conflicts({"a": {"x": 1}, "b": {"x": 1}}) == []
    assert coordinator.detect_conflicts({"a": {"x": 1}, "b": {"y": 2}}) == []


def test_aggregate_partial_and_failed_statuses():
    coordinator, _, _ = build_coordinator()
    partial = coordinator.aggregate(
        "t1", {"a": {"answer": 1}, "b": RuntimeError("boom")}
    )
    assert partial.status == AGGREGATION_PARTIAL
    failed = coordinator.aggregate(
        "t2", {"a": RuntimeError("x"), "b": ValueError("y")}
    )
    assert failed.status == AGGREGATION_FAILED
    empty = coordinator.aggregate("t3", {})
    assert empty.status == AGGREGATION_FAILED


# --------------------------------------------------------------------------- #
# Coordinator: inventory
# --------------------------------------------------------------------------- #
def test_inventory_reports_graph_agents_and_counters():
    coordinator, clock, _ = build_coordinator()
    first = coordinator.delegate(
        parent="a", child="b", task_id="task-1", payload={}
    )
    clock.advance(1.0)
    second = coordinator.delegate(
        parent="b", child="c", task_id="task-1", payload={}, depth=1
    )
    coordinator.accept(first.delegation_id)

    inventory = coordinator.inventory()
    assert inventory["delegations"] == 2
    assert inventory["messages"] == 3
    assert inventory["by_status"] == {"accepted": 1, "pending": 1}
    assert inventory["agents_per_task"] == {"task-1": ["a", "b", "c"]}
    graph = inventory["graph"]
    assert [edge["depth"] for edge in graph] == [0, 1]
    assert graph[0]["delegation_id"] == first.delegation_id
    assert graph[1]["delegation_id"] == second.delegation_id
    assert graph[1]["child"] == "c"
    assert inventory["children"] == {"a": ["b"], "b": ["c"]}
    assert len(inventory["records"]) == 2
    assert inventory["limits"]["max_delegation_depth"] == 2
    assert inventory["limits"]["max_total_delegations"] == 12
