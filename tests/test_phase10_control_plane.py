"""Phase 10 control-plane integration tests.

These exercise the real Phase 9 runtime (planner, selector, governor, executor,
audit) with a recording MATLAB transport.  Nothing here touches MATLAB.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from matlab_os.integration.matlab_policy import MatlabToolPolicy
from matlab_os.orchestration.control_plane import (
    MATLAB_CAPABILITIES,
    LocalAIOSControlPlane,
    build_control_plane,
)
from matlab_os.phase9.planning.model import ExecutionStatus
from matlab_os.phase9.config import Phase9Config


def status_of(result) -> ExecutionStatus:
    """Resolve an execution status to the enum, whether str or enum."""
    value = result.status
    if isinstance(value, ExecutionStatus):
        return value
    return ExecutionStatus(str(value))

def pinned_config(root):
    """Pin resource budgets so admission does not depend on host CPU load."""
    base = Phase9Config.default(str(root))
    return replace(
        base,
        resources=replace(
            base.resources,
            max_cpu_load_percent=100.0,
            min_free_ram_gb=0.0,
            fail_closed_without_telemetry=False,
        ),
    )


class Recorder:
    """Records transport calls and returns deterministic output."""

    def __init__(self, output=None):
        self.calls = []
        self.output = (
            output
            if output is not None
            else {"content": [{"type": "text", "text": "ok"}]}
        )

    def __call__(self, tool_name, arguments):
        self.calls.append((tool_name, dict(arguments)))
        return self.output


@pytest.fixture()
def plane(tmp_path):
    recorder = Recorder()
    control = build_control_plane(
        tmp_path / "ws", transport=recorder, config=pinned_config(tmp_path / "ws")
    )
    return control, recorder


def test_control_plane_registers_the_matlab_agent(plane):
    control, _ = plane
    health = control.health()
    assert health["control_plane"] == "local-ai-os"
    assert "matlab" in health["agents"]
    assert health["matlab_policy"]["posture"] == "read-only"


def test_all_canonical_capabilities_have_handlers(plane):
    control, _ = plane
    for capability in MATLAB_CAPABILITIES:
        assert control.runtime.handlers.get_handler(capability) is not None


def test_read_only_tool_runs_end_to_end(plane):
    control, recorder = plane
    result = control.run(
        "Analyse the model",
        matlab_tool="detect_matlab_toolboxes",
    )
    assert status_of(result) is ExecutionStatus.COMPLETED, result.to_dict()
    assert recorder.calls == [("detect_matlab_toolboxes", {})]
    assert result.to_dict()["status"] == "COMPLETED"


def test_mutating_tool_is_refused_and_never_reaches_matlab(plane):
    control, recorder = plane
    result = control.run("Edit the model", matlab_tool="model_edit")
    assert status_of(result) is not ExecutionStatus.COMPLETED
    assert recorder.calls == []


def test_unknown_tool_is_refused(plane):
    control, recorder = plane
    result = control.run("Do something", matlab_tool="os_shell")
    assert status_of(result) is not ExecutionStatus.COMPLETED
    assert recorder.calls == []


def test_code_execution_is_refused_under_read_only_policy(plane):
    control, recorder = plane
    result = control.run(
        "Run code",
        matlab_tool="evaluate_matlab_code",
        matlab_arguments={"code": "delete('x')"},
    )
    assert status_of(result) is not ExecutionStatus.COMPLETED
    assert recorder.calls == []


def test_authorize_is_side_effect_free(plane):
    control, recorder = plane
    decision = control.authorize("model_edit")
    assert decision["allowed"] is False
    assert recorder.calls == []


def test_denial_is_audited(plane):
    control, _ = plane
    control.run("Edit the model", matlab_tool="model_edit")
    audit_file = (
        Path(control.runtime.state_root) / "audit" / "phase9_audit.jsonl"
    )
    assert audit_file.exists()
    records = [
        json.loads(line)
        for line in audit_file.read_text(encoding="utf-8").splitlines()
    ]
    denials = [
        record
        for record in records
        if record.get("action") == "matlab.authorisation"
        and record.get("outcome") == "denied"
    ]
    assert denials, records
    assert denials[0]["details"]["tool"] == "model_edit"


def test_planning_without_a_tool_executes_nothing(plane):
    control, recorder = plane
    result = control.run("Document the approach")
    assert status_of(result) is ExecutionStatus.COMPLETED
    assert recorder.calls == []


def test_mutating_policy_can_be_enabled_explicitly(tmp_path):
    from matlab_os.integration.matlab_approval import MutationRequest, approval_target

    recorder = Recorder()
    control = build_control_plane(
        tmp_path / "ws2",
        transport=recorder,
        allow_mutating=True,
        config=pinned_config(tmp_path / "ws2"),
    )
    arguments = {"model": "m.slx"}
    pending = control.approval.request_approval(
        MutationRequest(
            tool="model_edit",
            arguments=arguments,
            target=approval_target(arguments, control.workspace_root),
            actor="operator",
            request_id="req-edit",
        )
    )
    ticket = control.approval.approve(pending.token_id, "raju")

    result = control.run(
        "Edit the model",
        matlab_tool="model_edit",
        matlab_arguments=arguments,
        request_id="req-edit",
        approval_ticket=ticket,
    )
    assert status_of(result) is ExecutionStatus.COMPLETED, result.to_dict()
    # One request executes exactly one MATLAB operation, even though the
    # plan expands into several steps.
    assert recorder.calls == [("model_edit", arguments)]


def test_policy_is_read_only_by_default(tmp_path):
    control = LocalAIOSControlPlane(
        workspace_root=tmp_path / "ws3", config=pinned_config(tmp_path / "ws3")
    )
    assert isinstance(control.policy, MatlabToolPolicy)
    assert control.policy.allow_mutating is False
    assert control.policy.allow_code_execution is False


def test_transport_absent_is_reported_not_silently_executed(tmp_path):
    control = LocalAIOSControlPlane(
        workspace_root=tmp_path / "ws4", config=pinned_config(tmp_path / "ws4")
    )
    result = control.run("Analyse the model", matlab_tool="model_read")
    assert status_of(result) is ExecutionStatus.COMPLETED
    reasons = [record.get("reason", "") for record in control.trace()]
    assert any("No MATLAB transport configured" in reason for reason in reasons), reasons


def test_one_request_executes_the_operation_once(plane):
    control, recorder = plane
    result = control.run(
        "Analyse and verify the model",
        matlab_tool="model_read",
        matlab_arguments={"model": "m.slx", "scope": "root", "depth": "0"},
    )
    assert status_of(result) is ExecutionStatus.COMPLETED
    assert len(recorder.calls) == 1, recorder.calls
    executed = [r for r in control.trace() if r.get("executed")]
    assert len(executed) == 1
