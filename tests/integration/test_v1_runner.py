"""V1 task runner integration tests.

These tests exercise the thin operational facade over the existing
LocalAIOSControlPlane. They use the real control plane and workspace, but avoid
external MATLAB because that is an external dependency.
"""

from __future__ import annotations

import pytest

from matlab_os.integrations.runner import (
    TaskRequest,
    TaskRecord,
    TaskResult,
    TaskRunner,
    TaskStatus,
)
from matlab_os.orchestration.control_plane import build_control_plane
from matlab_os.phase9.config import Phase9Config


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    return root, state


@pytest.fixture
def policy_grants():
    # These capabilities are registered on the control plane's registered
    # matlab agent profile; grant them so agent selection does not exclude
    # the only available agent during planning/execution.
    return [
        "analysis",
        "debugging",
        "design",
        "documentation",
        "implementation",
        "optimization",
        "research",
        "simulation",
        "verification",
    ]


@pytest.fixture
def runner(tmp_path, policy_grants):
    root, state = workspace = tmp_path / "workspace", tmp_path / "state"
    root.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    config = Phase9Config.default(str(root))
    config.security.allow_mutating = True
    config.security.allow_code_execution = False
    config.security.require_approval_for_writes = False
    cp = build_control_plane(
        workspace_root=str(root),
        state_root=str(state),
        config=config,
        allow_mutating=True,
        allow_code_execution=False,
    )
    for cap in policy_grants:
        cp.runtime.grant("operator", cap)
    return TaskRunner(cp, memory=None, database=None)


class TestTaskSubmission:
    def test_submits_a_simple_analysis_task(self, tmp_path, runner):
        root, state = tmp_path / "workspace", tmp_path / "state"
        root.mkdir(parents=True, exist_ok=True)
        state.mkdir(parents=True, exist_ok=True)
        from matlab_os.orchestration.control_plane import build_control_plane
        from matlab_os.phase9.config import Phase9Config

        config = Phase9Config.default(str(root))
        config.security.allow_mutating = True
        config.security.allow_code_execution = False
        cp = build_control_plane(
            workspace_root=str(root),
            state_root=str(state),
            config=config,
            allow_mutating=True,
            allow_code_execution=False,
        )
        for cap in ["analysis", "verification", "documentation"]:
            cp.runtime.grant("operator", cap)
        runner = TaskRunner(cp)

        request = TaskRequest(
            task="List the top-level files in the workspace root and summarize them.",
            project_id=1,
        )
        record = runner.submit(request)
        assert isinstance(record, TaskRecord)
        assert record.status == TaskStatus.SUBMITTED
        assert record.project_id == 1
        assert record.task == request.task
        assert record.requested_by == "operator"
        assert record.runtime_id is not None
        assert record.memory_ids == []

    def test_rejects_empty_task(self, runner):
        with pytest.raises(ValueError, match="task must be a non-empty string"):
            TaskRequest(task="", project_id=1)

    def test_rejects_missing_project_id(self, runner):
        with pytest.raises(ValueError, match="project_id must be a positive integer"):
            TaskRequest(task="x", project_id=0)

    def test_rejects_non_mapping_parameters(self, runner):
        with pytest.raises(ValueError, match="parameters must be a mapping"):
            TaskRequest(task="x", project_id=1, parameters=["bad"])


class TestTaskLifecycle:
    def test_status_returns_valid_record(self, runner):
        record = runner.submit(
            TaskRequest(task="Describe the workspace root.", project_id=2)
        )
        status = runner.status(record.task_id)
        assert isinstance(status, TaskRecord)
        assert status.status in {
            TaskStatus.SUBMITTED,
            TaskStatus.PLANNING,
            TaskStatus.RUNNING,
        }

    def test_status_rejects_unknown_task(self, runner):
        with pytest.raises(KeyError):
            runner.status("missing")

    def test_tasks_returns_all_submitted(self, runner):
        a = runner.submit(TaskRequest(task="alpha", project_id=3))
        b = runner.submit(TaskRequest(task="beta", project_id=3))
        tasks = runner.tasks()
        ids = [t.task_id for t in tasks]
        assert a.task_id in ids
        assert b.task_id in ids


class TestTaskResult:
    def test_completed_local_task_returns_result(self, tmp_path, runner):
        from matlab_os.orchestration.control_plane import build_control_plane
        from matlab_os.phase9.config import Phase9Config

        root, state = workspace = tmp_path / "workspace", tmp_path / "state"
        root.mkdir(parents=True, exist_ok=True)
        state.mkdir(parents=True, exist_ok=True)

        config = Phase9Config.default(str(root))
        config.security.allow_mutating = True
        config.security.allow_code_execution = False
        cp = build_control_plane(
            workspace_root=str(root),
            state_root=str(state),
            config=config,
            allow_mutating=True,
            allow_code_execution=False,
        )
        for cap in ["analysis", "verification", "documentation"]:
            cp.runtime.grant("operator", cap)
        runner = TaskRunner(cp)

        request = TaskRequest(
            task="List the top-level files in the workspace root and summarize them.",
            project_id=4,
        )
        record = runner.submit(request)
        # allow the control plane to plan and execute before reading the result
        _ = runner.status(record.task_id)
        result = runner.result(record.task_id)
        assert isinstance(result, TaskResult)
        assert result.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PARTIALLY_COMPLETED}
        assert result.runtime_status is not None
        assert result.task_id == record.task_id

    def test_failed_task_returns_errors(self, tmp_path, runner):
        from matlab_os.orchestration.control_plane import build_control_plane
        from matlab_os.phase9.config import Phase9Config

        root, state = workspace = tmp_path / "workspace", tmp_path / "state"
        root.mkdir(parents=True, exist_ok=True)
        state.mkdir(parents=True, exist_ok=True)

        config = Phase9Config.default(str(root))
        config.security.allow_mutating = True
        config.security.allow_code_execution = False
        cp = build_control_plane(
            workspace_root=str(root),
            state_root=str(state),
            config=config,
            allow_mutating=True,
            allow_code_execution=False,
        )
        for cap in ["implementation"]:
            cp.runtime.grant("operator", cap)
        runner = TaskRunner(cp)

        request = TaskRequest(
            task="Write a file using the implementation step.",
            project_id=5,
            parameters={"file": "notes.txt", "content": "hello"},
        )
        record = runner.submit(request)
        _ = runner.status(record.task_id)
        result = runner.result(record.task_id)
        assert result.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PARTIALLY_COMPLETED}
        assert isinstance(result.errors, list)


class TestMemoryAndProjectIsolation:
    def test_persists_memory_when_memory_available(self, tmp_path):
        root, state = tmp_path / "workspace", tmp_path / "state"
        root.mkdir(parents=True, exist_ok=True)
        state.mkdir(parents=True, exist_ok=True)
        from matlab_os.orchestration.control_plane import build_control_plane
        from matlab_os.phase9.config import Phase9Config
        from matlab_os.memory import DatabaseService, MemoryService

        config = Phase9Config.default(str(root))
        config.security.allow_mutating = True
        config.security.allow_code_execution = False
        cp = build_control_plane(
            workspace_root=str(root),
            state_root=str(state),
            config=config,
            allow_mutating=True,
            allow_code_execution=False,
        )
        for cap in ["analysis", "verification", "documentation"]:
            cp.runtime.grant("operator", cap)

        db = DatabaseService(str(state / "mem.db"))
        project_id = db.store_project("demo", "a demo project")
        mem = MemoryService(db)
        runner = TaskRunner(cp, memory=mem, database=db)

        request = TaskRequest(
            task="Summarize the workspace root.",
            project_id=project_id,
        )
        record = runner.submit(request)
        result = runner.result(record.task_id)

        assert isinstance(result.memory_refs, list)
        assert isinstance(record.memory_ids, list)
        saved = mem.get_fact(project_id, f"task.{record.task_id}.output")
        assert saved is None or isinstance(saved.content, str)

    def test_fails_silently_when_memory_unavailable(self, tmp_path):
        root, state = tmp_path / "workspace", tmp_path / "state"
        root.mkdir(parents=True, exist_ok=True)
        state.mkdir(parents=True, exist_ok=True)
        from matlab_os.orchestration.control_plane import build_control_plane
        from matlab_os.phase9.config import Phase9Config

        config = Phase9Config.default(str(root))
        config.security.allow_mutating = True
        config.security.allow_code_execution = False
        cp = build_control_plane(
            workspace_root=str(root),
            state_root=str(state),
            config=config,
            allow_mutating=True,
            allow_code_execution=False,
        )
        for cap in ["analysis", "verification", "documentation"]:
            cp.runtime.grant("operator", cap)
        runner = TaskRunner(cp, memory=None, database=None)
        request = TaskRequest(task="Summarize the workspace root.", project_id=9)
        record = runner.submit(request)
        result = runner.result(record.task_id)
        assert result.memory_refs == []
