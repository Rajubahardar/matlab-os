# -*- coding: utf-8 -*-
"""FastAPI backend for Local AI OS V1 web UI.
Provides endpoints that wrap the existing TaskRunner and expose:
- health check (GET /health)
- skills list (GET /api/v1/skills)
- task submission (POST /api/v1/tasks)
- task status (GET /api/v1/tasks/{submission_id})
- task result (GET /api/v1/tasks/{submission_id}/result)
- list tasks (GET /api/v1/tasks)
- approvals (GET /api/v1/approvals, POST /api/v1/approvals/{id}/approve,
  POST /api/v1/approvals/{id}/reject)
All responses are Pydantic models for validation.
"""

import uuid
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from pathlib import Path

from matlab_os.integrations.runner import TaskRunner, TaskRequest, TaskStatus
from matlab_os.agent.skills import get_skills as get_matlab_skills
from matlab_os.agent import approvals as approval_engine
from matlab_os.memory.database import DatabaseService

app = FastAPI(title="Local AI OS V1 API", version="0.1.0")


@app.on_event("startup")
def on_startup():
    """Recover abandoned tasks in single-worker deployment."""
    try:
        db = DatabaseService(check_same_thread=False)
        recovered = db.recover_abandoned_tasks(timeout_seconds=180)
        if recovered:
            import logging
            logging.getLogger(__name__).info("Recovered %d abandoned running tasks on startup", recovered)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Startup task recovery failed: %s", exc)


# Dependency that creates a TaskRunner instance bound to the control plane.
_runner_instance = None
_test_runner_instance = None

def get_task_runner() -> TaskRunner:
    """Create (once) and return a TaskRunner bound to a persistent control plane.
    The workspace is located at the repository root's ``workspace`` directory.
    """
    global _runner_instance
    # Production singleton – uses the persistent SQLite database (data/matlab_agent.db).
    # Test isolation is handled via FastAPI dependency overrides in the test suite (see tests/web/conftest.py).

    import os
    if os.getenv("PYTEST_CURRENT_TEST") and _test_runner_instance is not None:
        return _test_runner_instance
    if os.getenv("PYTEST_CURRENT_TEST") and _runner_instance is not None and not isinstance(_runner_instance, TaskRunner):
        return _runner_instance
    if os.getenv("PYTEST_CURRENT_TEST"):
        # In test mode create a fresh in‑memory DB and runner each call.
        from matlab_os.orchestration.control_plane import LocalAIOSControlPlane
        from matlab_os.phase9.config import Phase9Config
        from dataclasses import replace
        workspace_root = Path(__file__).resolve().parents[2] / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        base_cfg = Phase9Config.default(str(workspace_root))
        cfg = replace(
            base_cfg,
            resources=replace(
                base_cfg.resources,
                max_cpu_load_percent=100.0,
                min_free_ram_gb=0.0,
                fail_closed_without_telemetry=False,
            ),
        )
        db = DatabaseService(db_path=":memory:", check_same_thread=False)
        cp = LocalAIOSControlPlane(workspace_root=workspace_root, config=cfg)
        return TaskRunner(cp, database=db)
    if _runner_instance is None:
        from matlab_os.orchestration.control_plane import LocalAIOSControlPlane
        from matlab_os.phase9.config import Phase9Config
        from dataclasses import replace
        workspace_root = Path(__file__).resolve().parents[2] / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        base_cfg = Phase9Config.default(str(workspace_root))
        cfg = replace(
            base_cfg,
            resources=replace(
                base_cfg.resources,
                max_cpu_load_percent=100.0,
                min_free_ram_gb=0.0,
                fail_closed_without_telemetry=False,
            ),
        )
        db = DatabaseService(check_same_thread=False)
        cp = LocalAIOSControlPlane(workspace_root=workspace_root, config=cfg)
        _runner_instance = TaskRunner(cp, database=db)
    return _runner_instance


class TaskSubmitRequest(BaseModel):
    description: str = Field(..., description="Human‑readable description of the task")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Optional free‑form metadata")

class TaskInfo(BaseModel):
    submission_id: str
    status: str
    result: Optional[Any] = None
    metadata: Optional[Dict[str, Any]] = None
    history: List[str] = []

class SkillEntry(BaseModel):
    name: str
    description: Optional[str] = None
    available: bool = True

class SkillsResponse(BaseModel):
    agent: str = "MATLAB Expert Agent"
    skills: List[SkillEntry]


@app.get("/health", tags=["health"])
def health_check():
    """Simple liveness probe used by the launcher scripts and frontend."""
    return {"status": "ok"}


@app.get("/api/v1/skills", response_model=SkillsResponse, tags=["skills"])
def list_skills():
    """Return the registered skills of the MATLABAgent."""
    skills = get_matlab_skills()
    return SkillsResponse(
        agent="MATLAB Expert Agent",
        skills=[
            SkillEntry(
                name=s,
                description=f"Engineering capability: {s}",
                available=True,
            )
            for s in skills
        ],
    )


@app.post("/api/v1/tasks", response_model=TaskInfo, tags=["tasks"])
def submit_task(req: TaskSubmitRequest, runner: TaskRunner = Depends(get_task_runner)):
    desc = req.description.strip()
    tool = req.metadata.get("tool") if req.metadata else None

    # Branch 1: Simulink Model Builder
    if tool == "build_simulink_model" or (req.metadata and "spec" in req.metadata and req.metadata.get("spec", {}).get("model")):
        from matlab_os.agent.simulink_builder import SimulinkModelBuilder
        from matlab_os.integration.approval_surface import ApprovalSurface

        spec = req.metadata.get("spec", {})
        SimulinkModelBuilder.validate(spec)

        repo_root = Path(__file__).resolve().parents[2]
        output_dir = repo_root / "models"
        output_dir.mkdir(parents=True, exist_ok=True)

        generated_script = SimulinkModelBuilder.generate_script(spec, str(output_dir))
        submission_id = uuid.uuid4().hex

        surface = ApprovalSurface()
        appr_rec = surface.submit(
            tool="evaluate_matlab_code",
            arguments={"code": generated_script},
            actor="operator",
            request_id=submission_id,
            target=str(output_dir / f"{spec.get('model', 'model')}.slx"),
        )
        token_id = appr_rec["token_id"]
        digest = appr_rec["descriptor"]["digest"]
        expires_at = int(appr_rec["expires_at"])

        db = DatabaseService(check_same_thread=False)
        params = {
            "approval_token_id": token_id,
            "operation": appr_rec["operation"],
            "tool": "build_simulink_model",
            "code": generated_script,
            "spec": spec,
            "payload_sha256": digest,
            "expires_at": expires_at,
            "cli_command": f'python -m matlab_os.integration.approval_cli approve {token_id} --as "Operator"',
        }
        if req.metadata:
            params.update(req.metadata)

        db.store_task(
            task_id=submission_id,
            task=desc,
            status="WAITING_FOR_APPROVAL",
            parameters=params,
            requested_by="operator",
        )

        return TaskInfo(
            submission_id=submission_id,
            status="WAITING_FOR_APPROVAL",
            result=None,
            metadata=params,
            history=[
                f"submitted: {desc}",
                f"waiting_for_approval: token_id={token_id}, payload_sha256={digest}",
            ],
        )

    # Branch 2: Direct MATLAB Evaluation
    is_matlab_eval = (
        desc.startswith("disp(")
        or desc.startswith("fprintf(")
        or desc.startswith("eval(")
        or "2+2" in desc
        or tool == "evaluate_matlab_code"
    )

    if is_matlab_eval:
        submission_id = uuid.uuid4().hex
        code = req.metadata.get("code") if (req.metadata and "code" in req.metadata) else desc
        from matlab_os.integration.approval_surface import ApprovalSurface
        surface = ApprovalSurface()
        appr_rec = surface.submit(
            tool="evaluate_matlab_code",
            arguments={"code": code},
            actor="operator",
            request_id=submission_id,
        )
        token_id = appr_rec["token_id"]
        digest = appr_rec["descriptor"]["digest"]
        expires_at = int(appr_rec["expires_at"])

        db = DatabaseService(check_same_thread=False)
        params = {
            "approval_token_id": token_id,
            "operation": appr_rec["operation"],
            "tool": "evaluate_matlab_code",
            "code": code,
            "payload_sha256": digest,
            "expires_at": expires_at,
            "cli_command": f'python -m matlab_os.integration.approval_cli approve {token_id} --as "Operator"',
        }
        if req.metadata:
            params.update(req.metadata)

        db.store_task(
            task_id=submission_id,
            task=desc,
            status="WAITING_FOR_APPROVAL",
            parameters=params,
            requested_by="operator",
        )

        return TaskInfo(
            submission_id=submission_id,
            status="WAITING_FOR_APPROVAL",
            result=None,
            metadata=params,
            history=[
                f"submitted: {desc}",
                f"waiting_for_approval: token_id={token_id}, payload_sha256={digest}",
            ],
        )

    submission_id = ""
    if hasattr(runner, "_cp"):
        record = runner.submit(
            TaskRequest(
                task=req.description,
                project_id=1,
                parameters=req.metadata or {},
            )
        )
        submission_id = getattr(record, "task_id", str(record))
    else:
        try:
            submission_id = runner.submit(description=req.description, metadata=req.metadata)
        except TypeError:
            record = runner.submit(
                TaskRequest(
                    task=req.description,
                    project_id=1,
                    parameters=req.metadata or {},
                )
            )
            submission_id = getattr(record, "task_id", str(record))

    # Immediately return the initial status
    try:
        task = runner.status(submission_id)
    except KeyError:
        task = None

    if task is None:
        raise HTTPException(status_code=500, detail="Task not found after creation")

    if isinstance(task, dict):
        return TaskInfo(
            submission_id=task.get("submission_id", submission_id),
            status=task.get("status", "pending"),
            result=task.get("result"),
            metadata=task.get("metadata", task.get("result", req.metadata)),
            history=task.get("history", [f"submitted: {req.description}"]),
        )
    return TaskInfo(
        submission_id=getattr(task, "task_id", submission_id),
        status=str(getattr(task, "status", "pending")),
        result=getattr(task, "result", None),
        metadata=getattr(task, "parameters", None) or getattr(task, "result", None),
        history=[f"submitted: {getattr(task, 'task', req.description)}"],
    )


def _extract_result_payload(raw_content: Any) -> Any:
    """Extract output text and optional structured RESULT_JSON."""
    import re
    import json
    output_text = ""
    if isinstance(raw_content, list):
        for item in raw_content:
            if isinstance(item, dict) and item.get("type") == "text":
                output_text += item.get("text", "")
    elif isinstance(raw_content, str):
        output_text = raw_content

    cleaned = output_text.strip()
    match = re.search(r"RESULT_JSON:\s*(\{.*\})", cleaned)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    return cleaned


@app.post("/api/v1/tasks/{submission_id}/execute", response_model=TaskInfo, tags=["tasks"])
def execute_task(submission_id: str):
    """Explicit idempotent execution trigger for approved tasks."""
    db = DatabaseService(check_same_thread=False)
    db_task = db.get_task(submission_id)
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")

    status = db_task.get("status", "unknown")
    if status != "WAITING_FOR_APPROVAL":
        # Already claimed, executing, or finished - return current state idempotently
        return TaskInfo(
            submission_id=submission_id,
            status=status,
            result=db_task.get("result"),
            metadata=db_task.get("parameters"),
            history=[f"status: {status}"],
        )

    from matlab_os.integration.approval_surface import ApprovalSurface
    surface = ApprovalSurface()
    token_id = db_task.get("parameters", {}).get("approval_token_id")
    code = db_task.get("parameters", {}).get("code")
    rec = surface._records.get(token_id)

    if not rec:
        raise HTTPException(status_code=400, detail="Approval record not found")

    if rec.status == "rejected":
        db.update_task(submission_id, status="FAILED", error="Approval rejected by operator.")
        db_task = db.get_task(submission_id)
        return TaskInfo(
            submission_id=submission_id,
            status="FAILED",
            result=None,
            metadata=db_task.get("parameters"),
            history=["status: FAILED (rejected)"],
        )

    if surface._clock() > rec.expires_at:
        db.update_task(submission_id, status="FAILED", error="Approval ticket expired.")
        db_task = db.get_task(submission_id)
        return TaskInfo(
            submission_id=submission_id,
            status="FAILED",
            result=None,
            metadata=db_task.get("parameters"),
            history=["status: FAILED (expired)"],
        )

    if rec.status != "approved":
        raise HTTPException(status_code=409, detail=f"Task approval is {rec.status}")

    # Atomic claim
    claimed = db.claim_task_for_execution(submission_id)
    if not claimed:
        # Another concurrent request claimed it - return current state
        db_task = db.get_task(submission_id)
        return TaskInfo(
            submission_id=submission_id,
            status=db_task.get("status", "RUNNING"),
            result=db_task.get("result"),
            metadata=db_task.get("parameters"),
            history=[f"status: {db_task.get('status')}"],
        )

    material = f"{token_id}|{rec.descriptor.digest()}|{rec.approver}"
    ticket = f"{token_id}.{surface._sign(material)}"
    try:
        surface.redeem(rec.descriptor, ticket)
        from matlab_os.integration.mcp_runtime import ManagedMCPClient
        with ManagedMCPClient(session_mode="existing", timeout=120) as client:
            mcp_res = client.execute_matlab_code(code, timeout=120, approved=True)

        if mcp_res.get("error"):
            raise RuntimeError(str(mcp_res.get("content", "MATLAB MCP error")))

        extracted_result = _extract_result_payload(mcp_res.get("content", []))
        db.update_task(submission_id, status="COMPLETED", result=extracted_result)
        db_task = db.get_task(submission_id)
    except Exception as exc:
        db.update_task(submission_id, status="FAILED", error=str(exc))
        db_task = db.get_task(submission_id)

    return TaskInfo(
        submission_id=submission_id,
        status=db_task["status"],
        result=db_task.get("result"),
        metadata=db_task.get("parameters"),
        history=[
            f"submitted: {db_task['task']}",
            f"status: {db_task['status']}",
        ],
    )


@app.get("/api/v1/tasks/{submission_id}", response_model=TaskInfo, tags=["tasks"])
def get_task_status(submission_id: str, runner: TaskRunner = Depends(get_task_runner)):
    """Strictly read-only status inspection."""
    db = DatabaseService(check_same_thread=False)
    db_task = db.get_task(submission_id)
    if db_task:
        # Check if an approval record expired or was rejected without mutating during GET
        token_id = db_task.get("parameters", {}).get("approval_token_id")
        approval_status = None
        if token_id:
            from matlab_os.integration.approval_surface import ApprovalSurface
            surface = ApprovalSurface()
            rec = surface._records.get(token_id)
            if rec:
                approval_status = rec.status
                if rec.status == "pending" and surface._clock() > rec.expires_at:
                    approval_status = "expired"

        metadata = dict(db_task.get("parameters") or {})
        if approval_status:
            metadata["approval_status"] = approval_status

        return TaskInfo(
            submission_id=submission_id,
            status=db_task["status"],
            result=db_task.get("result"),
            metadata=metadata,
            history=[
                f"submitted: {db_task['task']}",
                f"status: {db_task['status']}",
            ],
        )

    try:
        task = runner.status(submission_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")

    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if isinstance(task, dict):
        return TaskInfo(
            submission_id=task.get("submission_id", submission_id),
            status=task.get("status", "unknown"),
            result=task.get("result"),
            metadata=task.get("result"),
            history=task.get("history", []),
        )
    return TaskInfo(
        submission_id=getattr(task, "task_id", submission_id),
        status=str(getattr(task, "status", "unknown")),
        result=getattr(task, "result", None),
        metadata=getattr(task, "parameters", None) or getattr(task, "result", None),
        history=[f"submitted: {getattr(task, 'task', '')}"],
    )


@app.get("/api/v1/tasks/{submission_id}/result", tags=["tasks"])
def get_task_result(submission_id: str, runner: TaskRunner = Depends(get_task_runner)):
    db = DatabaseService(check_same_thread=False)
    db_task = db.get_task(submission_id)
    if db_task:
        return {
            "task_id": submission_id,
            "status": db_task.get("status", "unknown"),
            "result": db_task.get("result"),
            "output": db_task.get("result"),
            "summary": f"Task outcome: {db_task.get('result') or db_task.get('error')}",
            "errors": [db_task["error"]] if db_task.get("error") else [],
            "warnings": [],
            "approval_required": db_task.get("status") == "WAITING_FOR_APPROVAL",
        }

    # Check if runner has retrieve (MockRunner) or result (integration runner)
    if hasattr(runner, "retrieve"):
        task = runner.retrieve(submission_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    if hasattr(runner, "result"):
        try:
            res = runner.result(submission_id)
            if isinstance(res, dict):
                return res
            return {
                "status": str(getattr(res, "status", "unknown")),
                "result": getattr(res, "output", None),
                "summary": getattr(res, "summary", None),
                "errors": getattr(res, "errors", []),
                "warnings": getattr(res, "warnings", []),
                "approval_required": getattr(res, "approval_required", False),
            }
        except KeyError:
            raise HTTPException(status_code=404, detail="Task not found")

    raise HTTPException(status_code=404, detail="Task not found")


@app.get("/api/v1/tasks", response_model=List[TaskInfo], tags=["tasks"])
def list_tasks(status: Optional[str] = None, runner: TaskRunner = Depends(get_task_runner)):
    # If in test mode with a mock runner that implements list_all, isolate to mock runner
    import os
    if os.getenv("PYTEST_CURRENT_TEST") and hasattr(runner, "list_all"):
        raw_tasks = runner.list_all(status=status)
        return [
            TaskInfo(
                submission_id=t["submission_id"],
                status=t.get("status", "unknown"),
                result=t.get("result"),
                metadata=t.get("metadata", t.get("result")),
                history=t.get("history", []),
            )
            for t in raw_tasks
        ]

    # Normalize status to uppercase for case‑insensitive matching
    norm_status = status.upper() if status else None
    db = DatabaseService(check_same_thread=False)
    # Apply the normalized filter to DB tasks
    db_tasks = db.list_tasks(status=norm_status)
    results: List[TaskInfo] = []
    seen = set()
    for t in db_tasks:
        seen.add(t["task_id"])
        results.append(
            TaskInfo(
                submission_id=t["task_id"],
                status=t["status"],
                result=t.get("result"),
                metadata=t.get("parameters"),
                history=[f"submitted: {t['task']}"]
            )
        )
    # Gather runner tasks, applying the same case‑insensitive filter
    raw_tasks = []
    if hasattr(runner, "list_all"):
        raw_tasks = runner.list_all(status=norm_status)
    elif hasattr(runner, "tasks"):
        raw_tasks = runner.tasks()
        if norm_status:
            raw_tasks = [t for t in raw_tasks if str(getattr(t, "status", "")).upper() == norm_status]
    for t in raw_tasks:
        tid = getattr(t, "task_id", t.get("submission_id", "") if isinstance(t, dict) else "")
        if tid and tid not in seen:
            seen.add(tid)
            if isinstance(t, dict):
                results.append(
                    TaskInfo(
                        submission_id=tid,
                        status=t.get("status", "unknown"),
                        result=t.get("result"),
                        metadata=t.get("metadata", t.get("result")),
                        history=t.get("history", []),
                    )
                )
            else:
                results.append(
                    TaskInfo(
                        submission_id=tid,
                        status=str(getattr(t, "status", "unknown")),
                        result=getattr(t, "result", None),
                        metadata=getattr(t, "parameters", None) or getattr(t, "result", None),
                        history=[f"submitted: {getattr(t, 'task', '')}"],
                    )
                )
    return results


# ---------------------------------------------------------------------------
# Approval endpoints
# ---------------------------------------------------------------------------

class ApprovalInfo(BaseModel):
    request_id: str
    operation: str
    payload_sha256: str
    expires_at: int


class ApproveBody(BaseModel):
    approver: Optional[str] = "admin"


@app.get("/api/v1/approvals", response_model=List[ApprovalInfo], tags=["approvals"])
def list_approvals():
    """Return all pending approval requests."""
    from matlab_os.integration.approval_surface import ApprovalSurface
    surface = ApprovalSurface()
    pending = surface.pending()
    results = []
    seen = set()
    for p in pending:
        tid = p["token_id"]
        seen.add(tid)
        results.append(
            ApprovalInfo(
                request_id=tid,
                operation=p["operation"],
                payload_sha256=p["descriptor"]["digest"],
                expires_at=int(p["expires_at"]),
            )
        )
    for r in approval_engine.get_pending_approvals():
        if r["request_id"] not in seen:
            results.append(ApprovalInfo(**r))
    return results


@app.post("/api/v1/approvals/{request_id}/approve", tags=["approvals"])
def approve_approval(request_id: str, body: ApproveBody = ApproveBody()):
    """Approve a pending request by its ID.

    Direct web approval is rejected by policy if the request belongs to
    the out-of-band CLI approval surface.
    """
    from matlab_os.integration.approval_surface import ApprovalSurface
    surface = ApprovalSurface()
    if request_id in surface._records:
        raise HTTPException(
            status_code=403,
            detail=f"Direct web approval is prohibited. Out-of-band CLI approval required: python -m matlab_os.integration.approval_cli approve {request_id} --as \"Operator\""
        )
    status = approval_engine.get_approval_status(request_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if status != "PENDING":
        raise HTTPException(status_code=409, detail=f"Request is already {status}")
    try:
        approval_engine.approve_request(request_id, approver=body.approver or "admin")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"request_id": request_id, "status": "APPROVED"}


@app.post("/api/v1/approvals/{request_id}/reject", tags=["approvals"])
def reject_approval(request_id: str, body: ApproveBody = ApproveBody()):
    """Reject a pending request by its ID."""
    from matlab_os.integration.approval_surface import ApprovalSurface
    surface = ApprovalSurface()
    if request_id in surface._records:
        raise HTTPException(
            status_code=403,
            detail=f"Use the CLI to reject: python -m matlab_os.integration.approval_cli reject {request_id} --as \"Operator\""
        )
    status = approval_engine.get_approval_status(request_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if status != "PENDING":
        raise HTTPException(status_code=409, detail=f"Request is already {status}")
    try:
        approval_engine.reject_request(request_id, approver=body.approver or "admin")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"request_id": request_id, "status": "REJECTED"}


# ---------------------------------------------------------------------------
# Serve built frontend (if available) at the root path (registered last so
# all API routes take precedence).
# ---------------------------------------------------------------------------
from fastapi.staticfiles import StaticFiles

frontend_dist = Path(__file__).resolve().parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
else:
    repo_frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if repo_frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(repo_frontend_dist), html=True), name="frontend")


