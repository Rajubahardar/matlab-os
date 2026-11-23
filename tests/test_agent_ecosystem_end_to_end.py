"""Phase 12 end-to-end workflow through the real control plane.

Path under test::

    Task -> Agent Registry -> Capability Selection -> Lifecycle
         -> Local AI OS control plane (policy + audit)
         -> existing MATLAB agent -> validated ResultContract

Only the MATLAB process boundary is stubbed: the control plane, its policy,
planner, selector, governor, executor, audit log, the Phase 12 registry,
selection adapter and lifecycle are the real implementations.
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
from matlab_os.orchestration.control_plane import MATLAB_CAPABILITIES, LocalAIOSControlPlane
from matlab_os.phase9.agents.capability import AgentCapability, AgentProfile
from matlab_os.phase9.audit import NullAuditLog
from matlab_os.phase9.collaboration.coordinator import CollaborationCoordinator
from matlab_os.phase9.config import Phase9Config


class Recorder:
    """Records transport calls; the only stubbed boundary is MATLAB itself."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, tool_name, arguments):
        self.calls.append((tool_name, dict(arguments)))
        return {"content": [{"type": "text", "text": "ok"}]}


def matlab_profile() -> AgentProfile:
    return AgentProfile(
        agent_id="matlab",
        display_name="MATLAB Expert Agent",
        capabilities={
            name: AgentCapability(
                name=name,
                tools=("workspace", "model", "matlab_mcp"),
                max_complexity=4,
            )
            for name in MATLAB_CAPABILITIES
        },
        tools=frozenset({"workspace", "model", "matlab_mcp"}),
        permission_scope=frozenset({"workspace.read", "workspace.write", "model.invoke"}),
        max_complexity=4,
        max_concurrent_tasks=1,
        locality="local",
    )


@pytest.fixture()
def ecosystem(tmp_path):
    recorder = Recorder()
    # Pin resource budgets: admission must not depend on the host's live CPU
    # load, which would make the workflow result nondeterministic.
    base_config = Phase9Config.default(str(tmp_path / "workspace"))
    config = replace(
        base_config,
        resources=replace(
            base_config.resources,
            max_cpu_load_percent=100.0,
            min_free_ram_gb=0.0,
            fail_closed_without_telemetry=False,
        ),
    )
    plane = LocalAIOSControlPlane(
        workspace_root=tmp_path / "workspace",
        state_root=tmp_path / "state",
        transport=recorder,
        config=config,
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
        "recorder": recorder,
        "plane": plane,
        "registry": registry,
        "memory": memory,
        "coordination": coordination,
    }


def test_end_to_end_real_workflow(ecosystem):
    executor = ecosystem["executor"]
    recorder = ecosystem["recorder"]
    memory = ecosystem["memory"]
    coordination = ecosystem["coordination"]

    task = ec.TaskContract(
        task_id="task-001",
        objective="Analyse the available MATLAB toolboxes",
        inputs={"model": "buck_converter"},
        required_capabilities=("analysis",),
        actor="operator",
    )

    workflow = executor.execute(
        task,
        required_tools=("workspace", "model", "matlab_mcp"),
        tool="detect_matlab_toolboxes",
        arguments={},
    )

    # The registered MATLAB agent was selected by capability, not hardcoded.
    assert workflow["agent_id"] == "matlab"
    assert workflow["selection"]["assigned_agent"] == "matlab"

    # The existing agent really executed through the control plane's transport.
    assert recorder.calls == [("detect_matlab_toolboxes", {})]
    assert workflow["dispatch"]["status"] == "running"

    # The result is a validated contract, verified by a real executed step.
    assert workflow["result"]["task_id"] == "task-001"
    assert workflow["result"]["agent_id"] == "matlab"
    assert workflow["result"]["status"] == "completed"
    assert workflow["result"]["verification_status"] == "verified"
    assert workflow["status"] == "completed"
    assert any(step.get("executed") for step in workflow["trace"])

    # Task-scoped memory was written by the real SQLite service.
    stored = memory.read_task_context("task-001", "operator")
    assert stored["context"]["agent_id"] == "matlab"
    assert stored["context"]["objective"] == task.objective
    assert stored["context"]["status"] == "completed"

    # The result is now a recorded upstream result with provenance.
    provenance = coordination.provenance("task-001")
    assert provenance["result"]["agent_id"] == "matlab"
    assert provenance["recorded_at"] is not None


def test_selection_rejects_unsatisfied_capability(ecosystem):
    executor = ecosystem["executor"]
    task = ec.TaskContract(
        task_id="task-002",
        objective="Do something no registered agent declares",
        inputs={},
        required_capabilities=("quantum_teleport",),
        actor="operator",
    )
    with pytest.raises(ec_execution.NoEligibleAgentError):
        executor.execute(task, tool="detect_matlab_toolboxes")

    assert ecosystem["recorder"].calls == []


def test_refused_tool_never_reaches_the_agent(ecosystem):
    executor = ecosystem["executor"]
    task = ec.TaskContract(
        task_id="task-003",
        objective="Edit the model without approval",
        inputs={},
        required_capabilities=("implementation",),
        actor="operator",
    )
    # A refusal is reported as a typed failed result, never as a raw throwable.
    workflow = executor.execute(task, tool="model_edit", arguments={"model": "buck_converter"})

    assert workflow["status"] == "failed"
    assert workflow["result"]["verification_status"] == "failed"
    assert workflow["result"]["agent_id"] == "matlab"
    # The mutating tool never reached the MATLAB transport.
    assert ecosystem["recorder"].calls == []
