import pytest
from fastapi.testclient import TestClient
from matlab_os.web import api

# Simple in‑memory mock runner that mimics the real TaskRunner API
class MockRunner:
    def __init__(self):
        self._store = {}
        self._counter = 0

    def submit(self, description: str, metadata: dict | None = None) -> str:
        self._counter += 1
        task_id = f"mock-{self._counter}"
        self._store[task_id] = {
            "submission_id": task_id,
            "status": "pending",
            "result": metadata or {},
            "history": [f"submitted: {description}"],
        }
        return task_id

    def status(self, submission_id: str):
        return self._store.get(submission_id)

    def retrieve(self, submission_id: str):
        task = self._store.get(submission_id)
        if task:
            return {"status": task["status"], "result": task["result"]}
        return None

    def list_all(self, status: str | None = None):
        tasks = list(self._store.values())
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        return tasks

# Replace the singleton runner with the mock before each test
@pytest.fixture(autouse=True)
def replace_runner(monkeypatch):
    # Ensure the module uses the mock runner instance
    mock_instance = MockRunner()
    monkeypatch.setattr(api, "_runner_instance", mock_instance)
    # get_task_runner will return this mock instance
    def get_mock_runner() -> MockRunner:
        return mock_instance
    monkeypatch.setattr(api, "get_task_runner", get_mock_runner)
    yield

client = TestClient(api.app)

def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

def test_skills():
    resp = client.get("/api/v1/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent"] == "MATLAB Expert Agent"
    assert len(data["skills"]) == 16
    assert any("Simulink" in s["name"] for s in data["skills"])

def test_submit_valid():
    payload = {"description": "Test task", "metadata": {"foo": "bar"}}
    resp = client.post("/api/v1/tasks", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "submission_id" in data
    assert data["status"] == "pending"
    assert data["metadata"] == {"foo": "bar"}
    # verify that task can be fetched
    sub_id = data["submission_id"]
    status_resp = client.get(f"/api/v1/tasks/{sub_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "pending"

def test_submit_invalid_missing_description():
    resp = client.post("/api/v1/tasks", json={"metadata": {}})
    assert resp.status_code == 422  # validation error from Pydantic

def test_get_unknown_task():
    resp = client.get("/api/v1/tasks/unknown-id")
    assert resp.status_code == 404

def test_result_endpoint():
    # create a task first
    payload = {"description": "Result test"}
    sub_id = client.post("/api/v1/tasks", json=payload).json()["submission_id"]
    # retrieve result (still pending)
    res = client.get(f"/api/v1/tasks/{sub_id}/result")
    assert res.status_code == 200
    result = res.json()
    assert result["status"] == "pending"
    assert result["result"] == {}

def test_list_tasks_and_filter():
    # create two tasks
    id1 = client.post("/api/v1/tasks", json={"description": "t1"}).json()["submission_id"]
    id2 = client.post("/api/v1/tasks", json={"description": "t2"}).json()["submission_id"]
    # list all
    all_resp = client.get("/api/v1/tasks")
    assert all_resp.status_code == 200
    all_tasks = all_resp.json()
    assert any(t["submission_id"] == id1 for t in all_tasks)
    assert any(t["submission_id"] == id2 for t in all_tasks)
    # filter by a status that does not exist (e.g., COMPLETED)
    filt = client.get("/api/v1/tasks", params={"status": "COMPLETED"})
    assert filt.status_code == 200
    assert filt.json() == []

def test_static_mount_absent_does_not_break_routes():
    # The test environment does not contain a frontend/dist directory.
    # Ensure a normal route works.
    resp = client.get("/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Approval endpoint tests
# ---------------------------------------------------------------------------

def test_list_approvals_empty(monkeypatch, tmp_path):
    """GET /api/v1/approvals returns an empty list when nothing is pending."""
    import tempfile, os
    from matlab_os.web import api
    from matlab_os.integration.approval_surface import ApprovalSurface
    # Isolate ApprovalSurface queue to empty temp path
    empty_q = tmp_path / "empty_approvals"
    monkeypatch.setattr("matlab_os.integration.approval_surface.DEFAULT_QUEUE", empty_q)
    # Point the approval engine at a temp DB so tests are isolated
    import matlab_os.agent.approvals as ap
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db = f.name
    f.close()
    old_default = ap.DEFAULT_DB
    old_cache = dict(ap._svc_cache)
    try:
        ap.DEFAULT_DB = tmp_db  # type: ignore[assignment]
        ap.close_services()
        resp = client.get("/api/v1/approvals")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        ap.close_services()
        ap.DEFAULT_DB = old_default  # type: ignore[assignment]
        ap._svc_cache.update(old_cache)
        os.unlink(tmp_db)


def test_approve_reject_lifecycle():
    """Create a request and walk through PENDING → APPROVED, verify one-use."""
    import tempfile, os
    from pathlib import Path
    import matlab_os.agent.approvals as ap

    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db = Path(f.name)
    f.close()

    old_default = ap.DEFAULT_DB
    old_cache = dict(ap._svc_cache)
    try:
        ap.DEFAULT_DB = tmp_db  # type: ignore[assignment]
        ap.close_services()
        # Create a request directly via the engine
        req_id = ap.create_approval_request(
            "eval", {"code": "disp('test')"}, db_path=tmp_db
        )

        # Verify it shows up in the pending list
        resp = client.get("/api/v1/approvals")
        assert resp.status_code == 200
        ids = [r["request_id"] for r in resp.json()]
        assert req_id in ids

        # Approve it (approver must differ from creator='agent')
        appr = client.post(f"/api/v1/approvals/{req_id}/approve", json={"approver": "tester"})
        assert appr.status_code == 200
        assert appr.json()["status"] == "APPROVED"

        # One-use: cannot approve again (status is now 'approved' → 409)
        appr2 = client.post(f"/api/v1/approvals/{req_id}/approve", json={"approver": "tester"})
        assert appr2.status_code == 409

        # Should no longer appear in pending list
        resp2 = client.get("/api/v1/approvals")
        ids2 = [r["request_id"] for r in resp2.json()]
        assert req_id not in ids2

        # Create another and reject it
        req_id2 = ap.create_approval_request("write_file", {"path": "/tmp/x"}, db_path=tmp_db)
        rej = client.post(f"/api/v1/approvals/{req_id2}/reject", json={"approver": "tester"})
        assert rej.status_code == 200
        assert rej.json()["status"] == "REJECTED"

        # Cannot reject a second time (status is now 'rejected' → 409)
        rej2 = client.post(f"/api/v1/approvals/{req_id2}/reject", json={"approver": "tester"})
        assert rej2.status_code == 409

    finally:
        ap.close_services()
        ap.DEFAULT_DB = old_default  # type: ignore[assignment]
        ap._svc_cache.update(old_cache)
        os.unlink(str(tmp_db))


def test_approve_unknown_request():
    """Approving a non-existent request returns 404."""
    # 'nonexistent-id' is not an integer — get_approval_status returns None → 404
    resp = client.post("/api/v1/approvals/nonexistent-id/approve", json={})
    assert resp.status_code == 404
