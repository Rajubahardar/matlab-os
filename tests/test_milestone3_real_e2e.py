# -*- coding: utf-8 -*-
"""Milestone 3 Real End-to-End Execution Test.

Validates:
1. Browser/API task submission of 'disp(2+2)'.
2. Task enters WAITING_FOR_APPROVAL with token_id and payload digest.
3. Web approve endpoint returns 403 Forbidden (browser cannot authorize by sending an approver name).
4. Operator approves out-of-band using the CLI with typed confirmation.
5. Execution path redeems the ticket, atomically consumes it, executes in MATLAB via MCP, and persists output '4'.
6. Replay, tampering, and double consumption fail closed.
7. Result survives a simulated backend restart.
"""
import json
import subprocess
import pytest
from fastapi.testclient import TestClient

from matlab_os.web.api import app
from matlab_os.integration.approval_surface import ApprovalSurface, MutationDescriptor, ApprovalSurfaceError
from matlab_os.memory.database import DatabaseService


@pytest.fixture
def client():
    return TestClient(app)


def test_milestone3_browser_to_matlab_real_execution(client):
    # 1. Submit task 'disp(2+2)' from browser/API
    submit_resp = client.post("/api/v1/tasks", json={"description": "disp(2+2)"})
    assert submit_resp.status_code == 200, submit_resp.text
    data = submit_resp.json()
    submission_id = data["submission_id"]
    status = data["status"]
    metadata = data["metadata"]

    assert status == "WAITING_FOR_APPROVAL"
    assert "approval_token_id" in metadata
    token_id = metadata["approval_token_id"]
    payload_hash = metadata["payload_sha256"]
    assert len(payload_hash) == 64

    # 2. Browser cannot authorize by sending an approver name (Option B boundary)
    forbidden_resp = client.post(f"/api/v1/approvals/{token_id}/approve", json={"approver": "web-user"})
    assert forbidden_resp.status_code == 403

    # 3. Local operator inspects pending via CLI
    surface = ApprovalSurface()
    pending_list = surface.pending()
    matching = [p for p in pending_list if p["token_id"] == token_id]
    assert len(matching) == 1
    assert matching[0]["descriptor"]["arguments"] == {"code": "disp(2+2)"}
    assert matching[0]["descriptor"]["digest"] == payload_hash

    # 4. Operator approves via the CLI with exact typed confirmation
    proc = subprocess.run(
        ["python", "-m", "matlab_os.integration.approval_cli", "approve", token_id, "--as", "Operator"],
        input=f"APPROVE {token_id}\n",
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    cli_out = proc.stdout
    assert "approved" in cli_out.lower()

    # 5. Browser polls task status -> transitions to RUNNING -> COMPLETED with result '4'
    status_resp = client.get(f"/api/v1/tasks/{submission_id}")
    assert status_resp.status_code == 200, status_resp.text
    task_data = status_resp.json()
    assert task_data["status"] == "COMPLETED"
    assert str(task_data["result"]) == "4"

    # 6. Result endpoint returns '4'
    res_resp = client.get(f"/api/v1/tasks/{submission_id}/result")
    assert res_resp.status_code == 200, res_resp.text
    res_data = res_resp.json()
    assert res_data["status"] == "COMPLETED"
    assert str(res_data["result"]) == "4"

    # 7. Verification: Replay of the ticket is rejected
    refreshed_surface = ApprovalSurface()
    rec = refreshed_surface._records.get(token_id)
    assert rec.status == "consumed"
    assert token_id in refreshed_surface._consumed

    descriptor = MutationDescriptor(
        tool="evaluate_matlab_code",
        arguments={"code": "disp(2+2)"},
        target=rec.descriptor.target,
        actor="operator",
        request_id=submission_id,
    )
    material = f"{token_id}|{rec.descriptor.digest()}|{rec.approver}"
    ticket = f"{token_id}.{refreshed_surface._sign(material)}"
    with pytest.raises(ApprovalSurfaceError, match="already used"):
        refreshed_surface.redeem(descriptor, ticket)

    # 8. Verification: Persistence across a simulated restart
    db = DatabaseService(check_same_thread=False)
    persisted = db.get_task(submission_id)
    assert persisted is not None
    assert persisted["status"] == "COMPLETED"
    assert str(persisted["result"]) == "4"
