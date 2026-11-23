"""End-to-end integration tests for the Phase 9 runtime.

These tests exercise the real composition of every layer — audit, approval,
path guard, workspace, resource governor, providers, directory, selector,
planner, replanner, executor and collaboration coordinator — against a real
temporary filesystem.  Nothing is mocked except the model transport.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from matlab_os.phase9.agents.capability import (
    AgentCapability,
    AgentProfile,
    AgentStatus,
)
from matlab_os.phase9.config import Phase9Config
from matlab_os.phase9.errors import (
    ApprovalRequiredError,
    DelegationDepthExceededError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
)
from matlab_os.phase9.planning.model import ExecutionStatus
from matlab_os.phase9.runtime.orchestrator import Phase9Runtime
from matlab_os.phase9.workspace.operations import WorkspaceManager


def make_profile(
    agent_id: str,
    capabilities,
    *,
    tools=("workspace", "model"),
    permissions=("workspace.read", "workspace.write", "model.invoke"),
    max_complexity: int = 4,
    max_concurrent_tasks: int = 2,
    locality: str = "local",
) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        display_name=agent_id.title(),
        capabilities={
            name: AgentCapability(name=name, tools=tuple(tools),
                                  max_complexity=max_complexity)
            for name in capabilities
        },
        tools=frozenset(tools),
        permission_scope=frozenset(permissions),
        max_complexity=max_complexity,
        max_concurrent_tasks=max_concurrent_tasks,
        locality=locality,
    )


FULL_CAPABILITIES = (
    "analysis",
    "design",
    "implementation",
    "verification",
    "documentation",
    "debugging",
)


@pytest.fixture()
def agent(runtime):
    profile = make_profile("engineering-agent", FULL_CAPABILITIES)
    runtime.register_agent(profile)
    return profile


def _writer_handler(runtime: Phase9Runtime, relative: str):
    """An implementation handler that writes through the guarded workspace."""

    def handler(step, context):
        workspace: WorkspaceManager = context["workspace"]
        record = workspace.write_text(
            relative,
            "# Result\n\nProduced by the Phase 9 implementation handler.\n",
            actor=context.get("agent_id", "agent:engineering"),
            approval_token=context.get("approval_token"),
            reason="phase9 integration",
        )
        return {"relative": record.relative, "sha256": record.sha256_after}

    return handler


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #
def test_runtime_builds_with_every_layer(runtime):
    health = runtime.health()
    assert health["status"] == "STAGING_ONLY"
    assert health["audit_chain_valid"] is True
    assert health["deepseek_enabled"] is False
    assert "analysis" in health["handlers"]


def test_runtime_status_is_serialisable(runtime, agent):
    status = runtime.status()
    payload = json.dumps(status)
    assert "workspace" in payload
    assert status["health"]["agents"] == 1


def test_full_pipeline_completes(runtime, agent):
    runtime.register_handler("implementation", _writer_handler(runtime, "out/report.md"))
    result = runtime.run(
        "Implement the report generator and verify it",
        context={"expect_files": ["out/report.md"]},
    )
    assert result.status == ExecutionStatus.COMPLETED.value, result.to_dict()
    assert (runtime.guard.root / "out" / "report.md").is_file()
    assert result.completed
    assert result.replans == 0


def test_full_pipeline_writes_valid_audit_chain(runtime, agent):
    runtime.register_handler("implementation", _writer_handler(runtime, "a.txt"))
    runtime.run("Implement the thing")
    audit = runtime.audit.summary()
    assert audit["chain"]["valid"] is True
    actions = audit["actions"]
    assert "task.submitted" in actions
    assert "plan.executed" in actions


def test_audit_file_contains_no_credentials(runtime, agent, monkeypatch):
    secret = "sk-integrationtest0123456789"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    runtime.register_handler("implementation", _writer_handler(runtime, "b.txt"))
    runtime.run("Implement the thing")
    content = (
        runtime.state_root / "audit" / "phase9_audit.jsonl"
    ).read_text(encoding="utf-8")
    assert secret not in content


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #
def test_agent_without_write_scope_cannot_reach_implementation(runtime):
    limited = make_profile(
        "readonly-agent",
        ("analysis", "verification"),
        permissions=("workspace.read",),
    )
    runtime.register_agent(limited)
    runtime.register_handler("implementation", _writer_handler(runtime, "c.txt"))
    result = runtime.run("Implement the thing and verify")
    assert result.status in (
        ExecutionStatus.FAILED.value,
        ExecutionStatus.PARTIAL.value,
        ExecutionStatus.AWAITING_APPROVAL.value,
    )
    assert not (runtime.guard.root / "c.txt").exists()
    failed = [step for step in result.plan.steps
              if step.status.value in {"failed", "skipped"}]
    assert failed


def test_revoked_grant_blocks_execution(runtime, agent):
    assert runtime.revoke("engineering-agent", "implementation") is True
    runtime.register_handler("implementation", _writer_handler(runtime, "d.txt"))
    result = runtime.run("Implement the thing")
    assert result.status in (
        ExecutionStatus.AWAITING_APPROVAL.value,
        ExecutionStatus.FAILED.value,
    )


def test_registration_cannot_grant_undeclared_capability(runtime):
    from matlab_os.phase9.errors import ConfigurationError

    limited = make_profile("tiny-agent", ("analysis",), permissions=())
    with pytest.raises(ConfigurationError):
        runtime.register_agent(limited, grants=["implementation"])


def test_unregistered_agent_is_denied(runtime):
    from matlab_os.phase9.errors import PermissionDeniedError

    profile = make_profile("ghost", ("analysis",), permissions=())
    runtime.directory.register(profile)
    with pytest.raises(PermissionDeniedError):
        runtime.check_step_permission(
            type("S", (), {"capability": "implementation", "step_id": "x"})(),
            "ghost",
        )


# --------------------------------------------------------------------------- #
# Capability-based selection
# --------------------------------------------------------------------------- #
def test_specialist_is_selected_for_a_capability_no_one_else_has(runtime, agent):
    specialist = make_profile(
        "simulation-specialist",
        ("simulation",),
        permissions=("workspace.read",),
        max_complexity=4,
    )
    runtime.register_agent(specialist)
    events: list[dict] = []
    result = runtime.run("Simulate the model", on_event=events.append)
    running = {
        event["step_id"]: event.get("agent_id")
        for event in events
        if event["event"] == "step.running"
    }
    assert "simulation-specialist" in set(running.values())
    assert any(
        step.capability == "simulation" and step.assigned_agent
        == "simulation-specialist"
        for step in result.plan.steps
    )


def test_selection_is_deterministic_for_equally_capable_agents(runtime):
    runtime.register_agent(make_profile("alpha-agent", ("analysis",), permissions=()))
    runtime.register_agent(make_profile("beta-agent", ("analysis",), permissions=()))
    events: list[dict] = []
    runtime.run("Analyse the repository", on_event=events.append)
    chosen = [
        event.get("agent_id") for event in events
        if event["event"] == "step.running"
    ]
    assert chosen and all(item == "alpha-agent" for item in chosen)


def test_selection_fails_when_no_agent_can_serve(runtime):
    profile = make_profile("analysis-only", ("analysis",), permissions=())
    runtime.register_agent(profile)
    result = runtime.run("Simulate and optimise the model")
    assert result.status in (
        ExecutionStatus.FAILED.value,
        ExecutionStatus.PARTIAL.value,
    )
    assert any(step.status.value == "failed" for step in result.plan.steps)


# --------------------------------------------------------------------------- #
# Approval gating inside execution
# --------------------------------------------------------------------------- #
def test_approval_gate_pauses_then_permits_execution(
    sandbox: Path, tmp_path: Path, healthy_monitor
):
    config = Phase9Config.default(str(sandbox / "approved"))
    config.planning.use_model_assistance = False
    config.security.require_approval_for_writes = True
    config.security.max_file_bytes = 100_000

    runtime = Phase9Runtime.build(
        config, state_root=tmp_path / "state2", monitor=healthy_monitor
    )
    try:
        runtime.register_agent(make_profile("engineering-agent", FULL_CAPABILITIES))
        runtime.register_agent(make_profile("analysis-agent", ("analysis",),
                                            permissions=()))
        runtime.register_handler("implementation", _writer_handler(runtime, "gated.txt"))

        first = runtime.run("Implement the gated artefact")
        assert first.status == ExecutionStatus.AWAITING_APPROVAL.value
        assert not (runtime.guard.root / "gated.txt").exists()

        # Operator grants the exact payload-bound approval.
        import hashlib

        content = (
            "# Result\n\nProduced by the Phase 9 implementation handler.\n"
        )
        binding = {
            "operation": "workspace.write",
            "relative": "gated.txt",
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
        request = runtime.approvals.request(
            "workspace.write", "gated.txt", binding, "agent:engineering-agent"
        )
        approval = runtime.approvals.approve(request.request_id, "human:owner")

        second = runtime.run(
            "Implement the gated artefact",
            context={"approval_token": approval.token},
        )
        assert (runtime.guard.root / "gated.txt").is_file()
        assert second.status in (
            ExecutionStatus.COMPLETED.value,
            ExecutionStatus.PARTIAL.value,
        )
    finally:
        runtime.shutdown()


# --------------------------------------------------------------------------- #
# Resource governance
# --------------------------------------------------------------------------- #
def test_resource_pressure_degrades_gracefully(sandbox: Path, tmp_path: Path):
    config = Phase9Config.default(str(sandbox / "pressure"))
    config.planning.use_model_assistance = False
    config.resources.min_free_ram_gb = 512.0
    runtime = Phase9Runtime.build(config, state_root=tmp_path / "state3")
    try:
        runtime.register_agent(make_profile("engineering-agent", FULL_CAPABILITIES))
        runtime.register_handler("implementation", _writer_handler(runtime, "x.txt"))
        result = runtime.run("Implement the thing")
        assert result.status in (
            ExecutionStatus.FAILED.value,
            ExecutionStatus.PARTIAL.value,
        )
        assert result.error or result.failed
    finally:
        runtime.shutdown()


def test_resource_status_is_reported(runtime):
    status = runtime.governor.status()
    assert "max_concurrent_agents" in json.dumps(status) or status


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
def test_disabled_deepseek_is_never_used(runtime):
    with pytest.raises((ProviderNotConfiguredError, ProviderUnavailableError)):
        runtime.providers.select(exclude=["ollama_local"])


def test_provider_health_reports_configuration(runtime):
    health = runtime.health()
    assert health["deepseek_enabled"] is False
    assert health["deepseek_credential_present"] is False


# --------------------------------------------------------------------------- #
# Collaboration
# --------------------------------------------------------------------------- #
def test_collaboration_respects_depth_limit(runtime, agent):
    runtime.register_agent(
        make_profile("sub-agent", ("analysis",), permissions=())
    )
    coordinator = runtime.coordinator
    record = coordinator.delegate(
        parent="engineering-agent",
        child="sub-agent",
        task_id="task-1",
        payload={"instruction": "analyse"},
        depth=0,
    )
    assert record.depth == 0
    with pytest.raises(DelegationDepthExceededError):
        coordinator.delegate(
            parent="engineering-agent",
            child="sub-agent",
            task_id="task-2",
            payload={"instruction": "again"},
            depth=runtime.config.collaboration.max_delegation_depth + 1,
        )


def test_collaboration_detects_conflicting_results(runtime):
    coordinator = runtime.coordinator
    results = {
        "agent-a": {"status": "ok", "value": 1},
        "agent-b": {"status": "ok", "value": 2},
    }
    conflicts = coordinator.detect_conflicts(results)
    assert conflicts
    aggregated = coordinator.aggregate("task-conflict", results)
    assert aggregated.status in {"CONFLICT", "conflict"}


# --------------------------------------------------------------------------- #
# Workspace boundary through the runtime
# --------------------------------------------------------------------------- #
def test_runtime_workspace_rejects_traversal(runtime, agent):
    from matlab_os.phase9.errors import PathSecurityError

    with pytest.raises(PathSecurityError):
        runtime.workspace.write_text("../escape.txt", "x", actor="agent")


def test_runtime_backup_directory_is_reserved(runtime, agent):
    from matlab_os.phase9.errors import PathSecurityError

    with pytest.raises(PathSecurityError):
        runtime.workspace.write_text(
            ".phase9_backups/manifest.jsonl", "tamper", actor="agent"
        )
