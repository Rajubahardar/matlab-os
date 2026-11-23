"""Minimal CLI surface for the V1 task runner.

This is intentionally small. It is a thin wrapper over
:func:`matlab_os.integrations.runner.TaskRunner` and the existing
:class:`matlab_os.orchestration.control_plane.LocalAIOSControlPlane`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from matlab_os.memory import DatabaseService, MemoryService
from matlab_os.orchestration.control_plane import build_control_plane
from matlab_os.phase9.config import Phase9Config

from matlab_os.integrations.runner import TaskRequest, TaskRunner

TASK_CATEGORIES = [
    "analysis",
    "implementation",
    "verification",
    "documentation",
    "debugging",
    "design",
    "optimization",
    "research",
    "simulation",
]

_TASK_INDEX = Path("workspace/.task_index.json")


def _ensure_dirs(workspace: Path, state: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)


def _load_index(workspace: Path) -> dict[str, str]:
    if not _TASK_INDEX.exists():
        return {}
    try:
        return json.loads(_TASK_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_index(workspace: Path, index: dict[str, str]) -> None:
    _TASK_INDEX.parent.mkdir(parents=True, exist_ok=True)
    _TASK_INDEX.write_text(json.dumps(index, indent=2), encoding="utf-8")


def _build_runner(state_root: Path, workspace_root: Path, *, mem_db: str | None = None):
    config = Phase9Config.default(str(workspace_root.resolve()))
    config.security.allow_mutating = True
    config.security.allow_code_execution = False
    cp = build_control_plane(
        workspace_root=str(workspace_root),
        state_root=str(state_root),
        config=config,
        allow_mutating=True,
        allow_code_execution=False,
    )
    for cap in TASK_CATEGORIES:
        cp.runtime.grant("operator", cap)
    db_path = mem_db or str(state_root / "mem.db")
    db = DatabaseService(db_path)
    project_id = db.store_project("cli-session", "default CLI session project")
    mem = MemoryService(db)

    return cp, TaskRunner(cp, memory=mem, database=db)


def _load_runner_cli(state_root: Path, workspace_root: Path):
    mem_db = str(state_root / "mem.db")
    cp, runner = _build_runner(state_root, workspace_root, mem_db=mem_db)
    return runner


def run_cli(argv=None):
    parser = argparse.ArgumentParser(
        prog="matlab-os",
        description="Local AI OS V1 task runner (CLI).",
    )
    sub = parser.add_subparsers(dest="command")

    submit = sub.add_parser("submit", help="Submit a task")
    submit.add_argument("task", nargs="+", help="Natural-language task description")
    submit.add_argument("--project", type=int, default=1, help="Project identifier")
    submit.add_argument("--category", default="", help="Optional task category")
    submit.add_argument("--params", default="{}", help="JSON execution parameters")
    submit.add_argument("--workspace", default=None, help="Workspace root path")
    submit.add_argument("--state", default=None, help="State root path")

    status = sub.add_parser("status", help="Get task status by id")
    status.add_argument("task_id")
    status.add_argument("--workspace", default=None, help="Workspace root path")
    status.add_argument("--state", default=None, help="State root path")

    result = sub.add_parser("result", help="Get task result by id")
    result.add_argument("task_id")
    result.add_argument("--workspace", default=None, help="Workspace root path")
    result.add_argument("--state", default=None, help="State root path")

    history = sub.add_parser("history", help="List recent tasks")
    history.add_argument("--workspace", default=None, help="Workspace root path")
    history.add_argument("--state", default=None, help="State root path")

    args = parser.parse_args(argv)

    workspace = Path(args.workspace or "workspace")
    state = Path(args.state or "state")
    _ensure_dirs(workspace, state)

    if args.command == "submit":
        mem_db = str(state / "mem.db")
        _, runner = _build_runner(state, workspace, mem_db=mem_db)
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as e:
            print(f"Invalid --params JSON: {e}")
            return 2
        record = runner.submit(
            TaskRequest(
                task=" ".join(args.task).strip(),
                project_id=args.project,
                category=args.category,
                parameters=params,
            )
        )
        print(f"task_id={record.task_id}")
        print(f"project_id={record.project_id}")
        print(f"runtime_id={record.runtime_id}")
        print(f"status={record.status}")
        print(f"submitted_at={record.submitted_at}")
        print(f"audit_ref={record.audit_ref}")
        return 0

    if args.command == "status":
        runner = _load_runner_cli(state, workspace)
        try:
            record = runner.status(args.task_id)
        except KeyError as e:
            print(str(e))
            return 3
        print(json.dumps(_encode_record(record), indent=2))
        return 0

    if args.command == "result":
        runner = _load_runner_cli(state, workspace)
        try:
            res = runner.result(args.task_id)
        except KeyError as e:
            print(str(e))
            return 3
        print(json.dumps(_encode_result(res), indent=2))
        return 0

    if args.command == "history":
        runner = _load_runner_cli(state, workspace)
        tasks = runner.tasks()
        print(json.dumps([_encode_record(t) for t in tasks], indent=2))
        return 0

    parser.print_help()
    return 1

    parser.print_help()
    return 1

    parser.print_help()
    return 1

    parser.print_help()
    return 1


def _encode_record(record):
    return {
        "task_id": record.task_id,
        "project_id": record.project_id,
        "task": record.task,
        "category": record.category,
        "status": record.status,
        "requested_by": record.requested_by,
        "submitted_at": record.submitted_at,
        "updated_at": record.updated_at,
        "awaiting_approval": record.awaiting_approval,
        "runtime_id": record.runtime_id,
        "output_locations": record.output_locations,
        "memory_ids": record.memory_ids,
        "audit_ref": record.audit_ref,
    }


def _encode_result(res):
    out = {
        "task_id": res.task_id,
        "status": res.status,
        "runtime_status": res.runtime_status,
        "selected_agent": res.selected_agent,
        "summary": res.summary,
        "output_locations": res.output_locations,
        "errors": res.errors,
        "warnings": res.warnings,
        "approval_required": res.approval_required,
        "execution_duration_seconds": res.execution_duration_seconds,
        "audit_ref": res.audit_ref,
        "memory_refs": res.memory_refs,
    }
    if res.output is not None:
        out["output"] = res.output
    return out


if __name__ == "__main__":
    raise SystemExit(run_cli())
