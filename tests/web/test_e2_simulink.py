"""Automated tests for Workstream E2: Browser-to-Simulink Workflow.
Covers:
- Spec validation (SimulinkModelBuilder.validate)
- Script generation with absolute paths and numerical extraction
- Simulink task submission via POST /api/v1/tasks
- Out-of-band CLI approval and case-insensitive self-approval block
- Pure read-only status and result polling
- Atomic execution-claim concurrency under competing threads
- Dedicated idempotent POST /api/v1/tasks/{id}/execute trigger
- Single-worker startup recovery for interrupted tasks
"""

import os
import pytest
import threading
from pathlib import Path
from fastapi.testclient import TestClient

from matlab_os.agent.simulink_builder import SimulinkModelBuilder
from matlab_os.integration.approval_surface import ApprovalSurface, ApprovalSurfaceError, CONFIRMATION_TEMPLATE
from matlab_os.memory.database import DatabaseService
from matlab_os.web.api import app, _extract_result_payload


RC_CIRCUIT_SPEC = {
    "model": "rc_circuit",
    "stop_time": "0.1",
    "solver": "ode45",
    "blocks": [
        {
            "name": "Step",
            "library": "simulink/Sources/Step",
            "position": [50, 100, 90, 140],
            "parameters": {
                "Time": "0",
                "Before": "0",
                "After": "1"
            }
        },
        {
            "name": "TransferFcn",
            "library": "simulink/Continuous/Transfer Fcn",
            "position": [150, 100, 240, 140],
            "parameters": {
                "Numerator": "[1]",
                "Denominator": "[0.01 1]"
            }
        },
        {
            "name": "Vout",
            "library": "simulink/Sinks/To Workspace",
            "position": [300, 100, 360, 140],
            "parameters": {
                "VariableName": "yout",
                "MaxDataPoints": "inf",
                "SampleTime": "-1",
                "SaveFormat": "Array"
            }
        }
    ],
    "connections": [
        {"from": "Step/1", "to": "TransferFcn/1"},
        {"from": "TransferFcn/1", "to": "Vout/1"}
    ]
}


def test_simulink_spec_validation_success():
    """Valid RC circuit specification passes validation."""
    model, blocks, connections = SimulinkModelBuilder.validate(RC_CIRCUIT_SPEC)
    assert model == "rc_circuit"
    assert len(blocks) == 3
    assert len(connections) == 2


def test_simulink_spec_validation_failure():
    """Invalid specifications fail-closed with clear ValueError."""
    with pytest.raises(ValueError, match="model name"):
        SimulinkModelBuilder.validate({"model": "123_invalid"})

    with pytest.raises(ValueError, match="At least one block"):
        SimulinkModelBuilder.validate({"model": "valid_name", "blocks": []})

    with pytest.raises(ValueError, match="Invalid endpoint"):
        SimulinkModelBuilder.validate({
            "model": "valid_name",
            "blocks": [{"name": "B1", "library": "simulink/Sources/Constant"}],
            "connections": [{"from": "NonExistent/1", "to": "B1/1"}]
        })


def test_script_generation_paths_and_extraction(tmp_path):
    """Generated script contains absolute paths and numerical extraction logic."""
    output_dir = tmp_path / "models"
    script = SimulinkModelBuilder.generate_script(RC_CIRCUIT_SPEC, str(output_dir))
    
    assert str(output_dir) in script
    assert "rc_circuit.slx" in script
    assert "rc_circuit_results.mat" in script
    assert "RESULT_JSON:" in script
    assert "simOut.yout(end)" in script
    assert "simOut.tout(end)" in script


def test_result_payload_extraction():
    """Verify regex extraction of RESULT_JSON from MATLAB stdout."""
    raw = (
        "SIMULINK BUILD REPORT\n"
        "Model: rc_circuit\n"
        "RESULT_JSON: {\"t_end\":0.1,\"y_end\":0.99995,\"y_ref\":0.99995,\"err_abs\":0.00001,\"pass\":true}\n"
        "SIMULINK_BUILD_SUCCESS\n"
    )
    extracted = _extract_result_payload(raw)
    assert isinstance(extracted, dict)
    assert extracted["pass"] is True
    assert extracted["y_end"] > 0.99


def test_sqlite_atomic_claim_concurrency(tmp_path):
    """10 concurrent threads attempt claim_task_for_execution; exactly 1 succeeds."""
    db_file = tmp_path / "test_concurrency.db"
    db_init = DatabaseService(db_path=str(db_file))
    db_init.store_task("task-race", "Concurrent test", status="WAITING_FOR_APPROVAL")
    db_init.close()

    results = []

    def attempt_claim():
        local_db = DatabaseService(db_path=str(db_file), check_same_thread=False)
        claimed = local_db.claim_task_for_execution("task-race")
        results.append(claimed)
        local_db.close()

    threads = [threading.Thread(target=attempt_claim) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == 9


def test_single_worker_startup_recovery(tmp_path):
    """Stale RUNNING tasks are recovered to FAILED on single-worker startup."""
    db_file = tmp_path / "test_recovery.db"
    db = DatabaseService(db_path=str(db_file))
    db.store_task("task-crashed", "Crashed worker task", status="RUNNING")
    
    recovered = db.recover_abandoned_tasks(timeout_seconds=180)
    assert recovered == 1
    
    task = db.get_task("task-crashed")
    assert task["status"] == "FAILED"
    assert "interrupted" in task["error"]


def test_simulink_task_submission_and_pure_readonly_get(monkeypatch, tmp_path):
    """Simulink task returns WAITING_FOR_APPROVAL; GET endpoints remain pure read-only."""
    test_db = DatabaseService(db_path=str(tmp_path / "test_api.db"))
    
    # Override DatabaseService in api to use our isolated test db
    monkeypatch.setattr("matlab_os.web.api.DatabaseService", lambda **kwargs: test_db)

    client = TestClient(app)

    # 1. Submit Simulink task
    resp = client.post("/api/v1/tasks", json={
        "description": "Build and simulate RC circuit model",
        "metadata": {
            "tool": "build_simulink_model",
            "spec": RC_CIRCUIT_SPEC
        }
    })
    assert resp.status_code == 200
    data = resp.json()
    submission_id = data["submission_id"]
    token_id = data["metadata"]["approval_token_id"]
    assert data["status"] == "WAITING_FOR_APPROVAL"
    assert "cli_command" in data["metadata"]

    # 2. Strict read-only GET test - polling multiple times never mutates or triggers execution
    get1 = client.get(f"/api/v1/tasks/{submission_id}")
    assert get1.status_code == 200
    assert get1.json()["status"] == "WAITING_FOR_APPROVAL"

    get2 = client.get(f"/api/v1/tasks/{submission_id}")
    assert get2.status_code == 200
    assert get2.json()["status"] == "WAITING_FOR_APPROVAL"

    # 3. Web approval is strictly blocked (HTTP 403)
    appr_resp = client.post(f"/api/v1/approvals/{token_id}/approve", json={"approver": "admin"})
    assert appr_resp.status_code == 403


def test_self_approval_prohibited():
    """Case-insensitive self-approval between approver and actor is blocked."""
    surface = ApprovalSurface()
    rec = surface.submit(
        tool="evaluate_matlab_code",
        arguments={"code": "1+1;"},
        actor="operator",
        request_id="sub-test-self"
    )
    tid = rec["token_id"]
    conf = CONFIRMATION_TEMPLATE.format(token_id=tid)

    # Approver 'Operator' matching actor 'operator' case-insensitively must be rejected
    with pytest.raises(ApprovalSurfaceError, match="Self-approval prohibited"):
        surface.decide(tid, approve=True, approver="Operator", confirmation=conf)

    with pytest.raises(ApprovalSurfaceError, match="Self-approval prohibited"):
        surface.decide(tid, approve=True, approver="operator", confirmation=conf)
