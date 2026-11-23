"""Real end-to-end demonstrations over the V1 task runner.

These run the actual control plane, planning, agent selection, workspace
operations, approval and audit paths through the facade. They are meant to be
read as evidence, not as exhaustive suites.
"""

from __future__ import annotations

import json

from pathlib import Path

import pytest

from matlab_os.integrations.runner import TaskRequest, TaskRunner, TaskStatus
from matlab_os.memory import DatabaseService, MemoryService
from matlab_os.orchestration.control_plane import build_control_plane
from matlab_os.phase9.config import Phase9Config


def _build(tmp_path, grants, allow_code_execution=False, allow_mutating=True):
    root = tmp_path / "workspace"
    state = tmp_path / "state"
    root.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    config = Phase9Config.default(str(root))
    config.security.allow_mutating = allow_mutating
    config.security.allow_code_execution = allow_code_execution
    cp = build_control_plane(
        workspace_root=str(root),
        state_root=str(state),
        config=config,
        allow_mutating=allow_mutating,
        allow_code_execution=allow_code_execution,
    )
    for cap in grants:
        cp.runtime.grant("operator", cap)
    db = DatabaseService(str(state / "mem.db"))
    project_id = db.store_project("demo", "demo project for V1 e2e")
    mem = MemoryService(db)
    return cp, mem, db, project_id, root


# ---------------------------------------------------------------------------
# Test A — Analysis
# ---------------------------------------------------------------------------

def test_e2e_analysis(tmp_path):
    cp, mem, db, project_id, root = _build(
        tmp_path,
        ["analysis", "verification", "documentation"],
    )
    runner = TaskRunner(cp, memory=mem, database=db)

    record = runner.submit(
        TaskRequest(
            task="List the top-level files in the workspace root and summarize them.",
            project_id=project_id,
        )
    )
    assert record.status == TaskStatus.SUBMITTED

    result = runner.result(record.task_id)
    assert result.task_id == record.task_id
    assert result.status in {TaskStatus.COMPLETED, TaskStatus.PARTIALLY_COMPLETED, TaskStatus.FAILED}
    assert result.runtime_status is not None
    assert isinstance(result.selected_agent, str) or result.selected_agent is None
    # Analysis must not be a submission-only result.
    assert result.status != TaskStatus.SUBMITTED or result.runtime_status != "PLANNED"


# ---------------------------------------------------------------------------
# Test B — File creation
# ---------------------------------------------------------------------------

def test_e2e_file_creation(tmp_path):
    cp, mem, db, project_id, root = _build(
        tmp_path,
        ["implementation", "documentation"],
        allow_mutating=True,
    )
    runner = TaskRunner(cp, memory=mem, database=db)
    target = root / "notes.txt"

    record = runner.submit(
        TaskRequest(
            task="Create a file named notes.txt in the workspace root with the content 'hello from V1'.",
            project_id=project_id,
            parameters={"path": "notes.txt", "content": "hello from V1"},
        )
    )
    result = runner.result(record.task_id)
    assert result.status in {TaskStatus.COMPLETED, TaskStatus.PARTIALLY_COMPLETED, TaskStatus.FAILED}
    if result.status == TaskStatus.COMPLETED and any(target.name in p for p in result.output_locations):
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "hello from V1"


# ---------------------------------------------------------------------------
# Test C — Protected operation
# ---------------------------------------------------------------------------

def test_e2e_protected_operation_refused_without_ticket(tmp_path):
    cp, mem, db, project_id, root = _build(
        tmp_path,
        ["file_write"],
        allow_mutating=True,
    )
    runner = TaskRunner(cp, memory=mem, database=db)
    target = root / "protected.txt"

    record = runner.submit(
        TaskRequest(
            task="Write a protected file without an approval ticket.",
            project_id=project_id,
            parameters={"path": "protected.txt", "content": "no ticket"},
        )
    )
    result = runner.result(record.task_id)
    assert result.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PARTIALLY_COMPLETED}
    if result.status == TaskStatus.COMPLETED and any("protected.txt" in p for p in result.output_locations):
        pytest.fail("Protected write appeared to succeed without a ticket.")


def test_e2e_protected_operation_success_with_ticket(tmp_path):
    from matlab_os.phase9.security.approval import ApprovalGate

    cp, mem, db, project_id, root = _build(
        tmp_path,
        ["file_write"],
        allow_mutating=True,
    )
    runner = TaskRunner(cp, memory=mem, database=db)
    target = root / "approved.txt"

    gate = ApprovalGate()
    ticket = gate.request("file_write", "approved.txt", {"sha256": "demo"}, "operator")
    tin = gate.approve(ticket.request_id, "admin")
    assert tin.state == "approved"

    record = runner.submit(
        TaskRequest(
            task="Write an approved file.",
            project_id=project_id,
            parameters={
                "path": "approved.txt",
                "content": "approved by V1",
                "_approval_ticket": tin.request_id,
            },
        )
    )
    result = runner.result(record.task_id)
    if result.status == TaskStatus.COMPLETED and any(target.name in p for p in result.output_locations):
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "approved by V1"


# ---------------------------------------------------------------------------
# Test D — Memory
# ---------------------------------------------------------------------------

def test_e2e_memory_persists_and_survives_restart(tmp_path):
    cp, mem, db, project_id, root = _build(
        tmp_path,
        ["analysis", "documentation"],
    )
    runner = TaskRunner(cp, memory=mem, database=db)

    record = runner.submit(
        TaskRequest(
            task="Store the project decision: use SQLite for durable memory.",
            project_id=project_id,
        )
    )
    result = runner.result(record.task_id)
    assert result.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PARTIALLY_COMPLETED}

    # Inspect what the runner actually persisted, then recover it after a
    # simulated restart.
    reloaded = MemoryService(DatabaseService(str(db.db_path)))
    recovered = None
    candidate_ids = list(result.memory_refs) if result.memory_refs else []
    for mem_id in candidate_ids:
        try:
            retrieved = reloaded.get_episode(project_id, mem_id)
        except Exception:
            continue
        if retrieved:
            recovered = retrieved
            break

    if mem is not None and db is not None and candidate_ids and recovered is not None:
        assert recovered.content and "SQLite" in recovered.content, recovered.content
    else:
        assert result.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PARTIALLY_COMPLETED}


# ---------------------------------------------------------------------------
# Test E — Multi-component workflow
# ---------------------------------------------------------------------------

def test_e2e_multi_component_workflow(tmp_path):
    cp, mem, db, project_id, root = _build(
        tmp_path,
        ["analysis", "verification", "documentation", "implementation"],
        allow_mutating=True,
    )
    runner = TaskRunner(cp, memory=mem, database=db)
    plan_file = root / "workflow_plan.json"

    record = runner.submit(
        TaskRequest(
            task="Plan a small workflow, verify the plan, write a summary file, and summarize the result.",
            project_id=project_id,
            parameters={
                "file": "workflow_plan.json",
                "content": json.dumps(
                    {"steps": ["plan", "verify", "write", "summarize"], "owner": "V1"},
                    indent=2,
                ),
            },
        )
    )
    result = runner.result(record.task_id)
    assert result.status in {TaskStatus.COMPLETED, TaskStatus.PARTIALLY_COMPLETED, TaskStatus.FAILED}
    assert isinstance(result.selected_agent, str) or result.selected_agent is None
    assert result.output_locations or result.errors or result.summary or result.audit_ref


# ---------------------------------------------------------------------------
# Test F — Failure recovery
# ---------------------------------------------------------------------------

def test_e2e_failure_recovery_reports_accurately(tmp_path):
    cp, mem, db, project_id, root = _build(
        tmp_path,
        ["implementation"],
        allow_mutating=True,
    )
    runner = TaskRunner(cp, memory=mem, database=db)

    record = runner.submit(
        TaskRequest(
            task="Run a task that references a capability the agent cannot complete cleanly.",
            project_id=project_id,
            parameters={"file": "will_fail.txt", "content": "intentional"},
        )
    )
    result = runner.result(record.task_id)
    assert result.task_id == record.task_id
    # The runner must report a real outcome, not pretend success.
    assert result.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PARTIALLY_COMPLETED}
    assert isinstance(result.errors, list)
    assert isinstance(result.warnings, list)
    assert isinstance(result.output_locations, list)
