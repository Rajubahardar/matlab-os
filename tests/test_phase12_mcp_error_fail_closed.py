"""Regression tests: an MCP failure must never look like a verified success.

A successful transport call is not a successful tool result.  These tests pin
the three distinct conditions apart:

* the transport was invoked;
* the tool returned a usable result;
* the task may be reported as completed and verified.

Only the MATLAB process boundary is stubbed; the control plane, policy,
planner, selector, governor, executor, registry, lifecycle, memory and
coordination are the real implementations.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from matlab_os.integration.agent_ecosystem import contract as ec
from matlab_os.integration.agent_ecosystem import coordination as ec_coordination
from matlab_os.integration.agent_ecosystem import execution as ec_execution
from matlab_os.integration.agent_ecosystem import lifecycle as ec_lifecycle
from matlab_os.integration.agent_ecosystem import memory_facade as ec_memory
from matlab_os.integration.agent_ecosystem import registry as ec_registry
from matlab_os.memory.database import DatabaseService
from matlab_os.orchestration import control_plane as cp
from matlab_os.orchestration.control_plane import (
    MATLAB_CAPABILITIES,
    LocalAIOSControlPlane,
)
from matlab_os.phase9.agents.capability import AgentCapability, AgentProfile
from matlab_os.phase9.audit import NullAuditLog
from matlab_os.phase9.collaboration.coordinator import CollaborationCoordinator
from matlab_os.phase9.config import Phase9Config

ATTACH_ERROR = {
    "error": True,
    "content": [{"type": "text", "text": "failed to attach to MATLAB session"}],
}
SUCCESS = {
    "error": False,
    "content": [{"type": "text", "text": "MATLAB Version: 24.2.0.2806996"}],
}
TOOLS = ("workspace", "model", "matlab_mcp")


def matlab_profile() -> AgentProfile:
    return AgentProfile(
        agent_id="matlab",
        display_name="MATLAB Expert Agent",
        capabilities={
            name: AgentCapability(name=name, tools=TOOLS, max_complexity=4)
            for name in MATLAB_CAPABILITIES
        },
        tools=frozenset(TOOLS),
        permission_scope=frozenset({"workspace.read", "workspace.write", "model.invoke"}),
        max_complexity=4,
        max_concurrent_tasks=1,
        locality="local",
    )


def pinned_config(root) -> Phase9Config:
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


def build_ecosystem(tmp_path, responder):
    calls: list[tuple[str, dict]] = []

    def transport(tool_name, arguments):
        calls.append((tool_name, dict(arguments)))
        return responder(tool_name, arguments)

    plane = LocalAIOSControlPlane(
        workspace_root=tmp_path / "workspace",
        state_root=tmp_path / "state",
        transport=transport,
        config=pinned_config(tmp_path / "workspace"),
    )
    registry = ec_registry.Registry(state_root=tmp_path / "registry", audit=NullAuditLog())
    lifecycle = ec_lifecycle.LifecycleManager(registry, state_root=tmp_path / "lifecycle")
    memory = ec_memory.MemoryFacade(
        DatabaseService(db_path=str(tmp_path / "memory.db")), workspace_root=tmp_path
    )
    coordination = ec_coordination.Coordination(
        CollaborationCoordinator(config=None, audit=NullAuditLog(), directory=None, clock=None)
    )
    executor = ec_execution.EcosystemExecutor(
        registry=registry,
        lifecycle=lifecycle,
        control_plane=plane,
        memory=memory,
        coordination=coordination,
    )
    executor.register_agent(matlab_profile())
    return {
        "executor": executor,
        "plane": plane,
        "memory": memory,
        "coordination": coordination,
        "calls": calls,
    }


def run_task(bundle, task_id="task-mcp-error"):
    task = ec.TaskContract(
        task_id=task_id,
        objective="Detect installed MATLAB toolboxes",
        inputs={},
        required_capabilities=("analysis",),
        actor="operator",
    )
    return bundle["executor"].execute(
        task,
        required_tools=TOOLS,
        tool="detect_matlab_toolboxes",
        arguments={},
    )


# --------------------------------------------------------------------- #
# 1. MCP error payload                                                  #
# --------------------------------------------------------------------- #
def test_error_payload_fails_step_and_task(tmp_path):
    bundle = build_ecosystem(tmp_path, lambda tool, args: ATTACH_ERROR)
    workflow = run_task(bundle)

    # The transport really was invoked exactly once.
    assert bundle["calls"] == [("detect_matlab_toolboxes", {})]

    # The step is not a successful execution and not verified.
    assert workflow["status"] == "failed"
    assert workflow["verification_status"] == "failed"
    assert workflow["result"]["status"] == "failed"
    assert workflow["result"]["verification_status"] != "verified"
    assert not any(step.get("executed") for step in workflow["trace"])
    assert workflow["result"]["resource_usage"]["executed_steps"] == 0

    # The failure detail from MATLAB is preserved, not swallowed.
    assert any(
        "failed to attach to MATLAB session" in error
        for error in workflow["result"]["errors"]
    )
    recorded = [step for step in bundle["plane"].trace() if step.get("failed")]
    assert recorded and "failed to attach to MATLAB session" in recorded[0]["error"]
    assert recorded[0]["executed"] is False
    assert recorded[0]["invoked"] is True


def test_direct_control_plane_run_reports_failure(tmp_path):
    bundle = build_ecosystem(tmp_path, lambda tool, args: ATTACH_ERROR)
    result = bundle["plane"].run(
        "Detect installed MATLAB toolboxes",
        matlab_tool="detect_matlab_toolboxes",
        matlab_arguments={},
        request_id="req-mcp-error",
    )

    assert str(getattr(result.status, "value", result.status)).lower() == "failed"
    assert result.completed == []
    assert result.failed == ["step-01"]
    assert "failed to attach to MATLAB session" in (result.error or "")
    assert bundle["calls"] == [("detect_matlab_toolboxes", {})]


# --------------------------------------------------------------------- #
# 2. Successful response still works                                    #
# --------------------------------------------------------------------- #
def test_successful_response_still_completes_and_verifies(tmp_path):
    bundle = build_ecosystem(tmp_path, lambda tool, args: SUCCESS)
    workflow = run_task(bundle, task_id="task-mcp-ok")

    assert bundle["calls"] == [("detect_matlab_toolboxes", {})]
    assert workflow["status"] == "completed"
    assert workflow["result"]["status"] == "completed"
    assert workflow["result"]["verification_status"] == "verified"
    assert workflow["result"]["resource_usage"]["executed_steps"] == 1
    assert workflow["result"]["errors"] == []
    assert any(step.get("executed") for step in workflow["trace"])


# --------------------------------------------------------------------- #
# 3. Transport exception                                                #
# --------------------------------------------------------------------- #
def test_transport_exception_fails_step_with_cause(tmp_path):
    def boom(tool, args):
        raise RuntimeError("mcp stream closed")

    bundle = build_ecosystem(tmp_path, boom)
    workflow = run_task(bundle, task_id="task-transport-error")

    assert bundle["calls"] == [("detect_matlab_toolboxes", {})]
    assert workflow["status"] == "failed"
    assert workflow["verification_status"] == "failed"
    assert not any(step.get("executed") for step in workflow["trace"])
    joined = " ".join(workflow["result"]["errors"])
    assert "RuntimeError" in joined
    assert "mcp stream closed" in joined


# --------------------------------------------------------------------- #
# 4. Malformed / unexpected response                                    #
# --------------------------------------------------------------------- #
def test_malformed_mcp_response_fails_closed(tmp_path):
    bundle = build_ecosystem(tmp_path, lambda tool, args: "ok")
    workflow = run_task(bundle, task_id="task-malformed")

    assert bundle["calls"] == [("detect_matlab_toolboxes", {})]
    assert workflow["status"] == "failed"
    assert workflow["verification_status"] == "failed"
    assert any(
        "unexpected MCP response type str" in error
        for error in workflow["result"]["errors"]
    )


# --------------------------------------------------------------------- #
# 5. Failed required step propagates to the overall task                #
# --------------------------------------------------------------------- #
def test_failed_required_step_propagates_to_task_status(tmp_path):
    bundle = build_ecosystem(tmp_path, lambda tool, args: ATTACH_ERROR)
    workflow = run_task(bundle, task_id="task-propagation")

    assert workflow["result"]["status"] == "failed"
    assert workflow["status"] == "failed"
    control_plane_status = workflow["result"]["output"]["control_plane"]["status"]
    assert str(getattr(control_plane_status, "value", control_plane_status)).lower() == "failed"
    assert not any(
        step.get("executed") and not step.get("failed")
        for step in workflow["trace"]
        if step.get("tool")
    )


# --------------------------------------------------------------------- #
# 6. Persistence                                                        #
# --------------------------------------------------------------------- #
def test_failed_execution_is_persisted_in_sqlite(tmp_path):
    bundle = build_ecosystem(tmp_path, lambda tool, args: ATTACH_ERROR)
    run_task(bundle, task_id="task-sqlite-failure")

    stored = bundle["memory"].read_task_context("task-sqlite-failure", "operator")
    assert stored["context"]["status"] == "failed"
    assert stored["context"]["verification_status"] == "failed"
    assert stored["context"]["agent_id"] == "matlab"


# --------------------------------------------------------------------- #
# 7. Coordination provenance retains the failure                        #
# --------------------------------------------------------------------- #
def test_coordination_preserves_failure_provenance(tmp_path):
    bundle = build_ecosystem(tmp_path, lambda tool, args: ATTACH_ERROR)
    run_task(bundle, task_id="task-coordination-failure")

    record = bundle["coordination"].failure("task-coordination-failure")
    assert record is not None
    assert record["result"]["status"] == "failed"
    assert record["result"]["verification_status"] == "failed"
    assert list(record["result"]["provenance"]) == ["matlab"]
    assert record["recorded_at"]

    # A failure is never handed off as fact.
    assert bundle["coordination"].provenance("task-coordination-failure")["result"] is None


# --------------------------------------------------------------------- #
# 8. No second invocation after failure                                 #
# --------------------------------------------------------------------- #
def test_no_second_invocation_after_failure(tmp_path):
    bundle = build_ecosystem(tmp_path, lambda tool, args: ATTACH_ERROR)
    workflow = run_task(bundle, task_id="task-single-call")

    assert len(bundle["calls"]) == 1
    invoked = [step for step in bundle["plane"].trace() if step.get("invoked")]
    assert len(invoked) == 1
    assert workflow["result"]["resource_usage"]["executed_steps"] == 0


# --------------------------------------------------------------------- #
# Reproduction: without error detection the defect is visible           #
# --------------------------------------------------------------------- #
def test_reproduction_error_payload_looked_successful_before_fix(tmp_path, monkeypatch):
    """The pre-fix behavior: an MCP error was recorded as executed output.

    Disabling only the error detector restores the old semantics, which proves
    this suite fails against the defect rather than merely describing the fix.
    """
    monkeypatch.setattr(cp, "mcp_error_message", lambda output: None)
    bundle = build_ecosystem(tmp_path, lambda tool, args: ATTACH_ERROR)
    workflow = run_task(bundle, task_id="task-reproduction")

    assert bundle["calls"] == [("detect_matlab_toolboxes", {})]
    assert workflow["status"] == "completed"
    assert workflow["result"]["verification_status"] == "verified"
    assert any(step.get("executed") for step in workflow["trace"])


# --------------------------------------------------------------------- #
# Detector unit behavior                                                #
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "payload",
    [
        {"error": True, "text": "failed to attach to MATLAB session"},
        {"isError": True, "content": [{"type": "text", "text": "boom"}]},
        {"is_error": True, "message": "boom"},
    ],
)
def test_detector_recognises_error_flags(payload):
    message = cp.mcp_error_message(payload)
    assert message is not None and message.strip()


@pytest.mark.parametrize("payload", [SUCCESS, {"content": []}, {"content": [{"type": "text", "text": "ok"}]}])
def test_detector_accepts_successful_payloads(payload):
    assert cp.mcp_error_message(payload) is None


def test_detector_treats_non_mapping_as_malformed():
    assert "unexpected MCP response type NoneType" in cp.mcp_error_message(None)
