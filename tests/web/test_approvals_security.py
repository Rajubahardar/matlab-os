# -*- coding: utf-8 -*-
"""Security-property unit tests for the approval engine.

Verifies that the canonical DatabaseService approval engine satisfies:
1. SHA-256 payload binding — stored hash matches the actual payload.
2. TTL — an expired request is not returned as pending.
3. One-use — once approved, re-use of ``use_approval`` returns False.
4. Reject blocks further use — a rejected approval cannot be used.
5. Self-approval prevention — requester cannot decide their own request.
"""
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from matlab_os.memory.database import DatabaseService, APPROVAL_TTL
import matlab_os.agent.approvals as ap


@pytest.fixture()
def tmp_db(tmp_path):
    """Provide a fresh temporary database for each test via the approvals module."""
    db = tmp_path / "test_agent.db"
    old = ap.DEFAULT_DB
    ap.DEFAULT_DB = db  # type: ignore[assignment]
    ap.close_services()
    yield db
    ap.close_services()
    ap.DEFAULT_DB = old  # type: ignore[assignment]


@pytest.fixture()
def svc(tmp_db):
    """Provide a DatabaseService bound to the same temp DB."""
    service = DatabaseService(db_path=str(tmp_db))
    yield service
    service.close()


# ---------------------------------------------------------------------------
# 1. SHA-256 binding
# ---------------------------------------------------------------------------

def test_sha256_binding(tmp_db, svc):
    """The stored hash must equal SHA-256(canonical payload string)."""
    payload = {"code": "disp('hello')", "timeout": 30}
    payload_str = json.dumps(payload, sort_keys=True)
    expected_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

    req_id = ap.create_approval_request("eval", payload, db_path=tmp_db)

    conn = sqlite3.connect(str(tmp_db))
    row = conn.execute(
        "SELECT payload_sha256 FROM approvals WHERE id=?", (int(req_id),)
    ).fetchone()
    assert row is not None, "Approval row not found"
    stored_hash = row[0]
    assert stored_hash == expected_hash, (
        f"Stored hash {stored_hash!r} != expected {expected_hash!r}"
    )


def test_sha256_different_payloads_differ(tmp_db):
    """Two distinct payloads must produce different stored hashes."""
    id1 = ap.create_approval_request("eval", {"code": "a"}, db_path=tmp_db)
    id2 = ap.create_approval_request("eval", {"code": "b"}, db_path=tmp_db)

    conn = sqlite3.connect(str(tmp_db))
    rows = dict(
        conn.execute(
            "SELECT id, payload_sha256 FROM approvals WHERE id IN (?,?)",
            (int(id1), int(id2)),
        ).fetchall()
    )
    assert rows[int(id1)] != rows[int(id2)]


# ---------------------------------------------------------------------------
# 2. TTL enforcement
# ---------------------------------------------------------------------------

def test_pending_list_excludes_expired(tmp_db):
    """Requests whose requested_at is older than APPROVAL_TTL must not appear."""
    req_id = ap.create_approval_request("eval", {"code": "x"}, db_path=tmp_db)

    # Backdate requested_at so the request appears expired
    past = (datetime.now(timezone.utc) - APPROVAL_TTL - timedelta(seconds=1)).isoformat()
    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "UPDATE approvals SET requested_at=? WHERE id=?", (past, int(req_id))
    )
    conn.commit()

    pending_ids = [r["request_id"] for r in ap.get_pending_approvals(db_path=tmp_db)]
    assert req_id not in pending_ids, (
        "Expired approval request was returned as PENDING — TTL not enforced."
    )


def test_pending_list_includes_fresh(tmp_db):
    """A newly created request must appear in the pending list."""
    req_id = ap.create_approval_request("eval", {"code": "fresh"}, db_path=tmp_db)
    pending_ids = [r["request_id"] for r in ap.get_pending_approvals(db_path=tmp_db)]
    assert req_id in pending_ids


# ---------------------------------------------------------------------------
# 3. One-use semantics (via DatabaseService.use_approval)
# ---------------------------------------------------------------------------

def test_use_approval_one_use(tmp_db, svc):
    """use_approval returns True the first time and False on replay."""
    payload_str = json.dumps({"code": "y"}, sort_keys=True)
    approval_id = svc.store_approval("eval", payload=payload_str, requested_by="agent")
    svc.approve_operation(approval_id, approved=True, decided_by="admin")

    assert svc.use_approval(approval_id, payload_str) is True
    assert svc.use_approval(approval_id, payload_str) is False


def test_use_approval_wrong_hash(tmp_db, svc):
    """use_approval returns False when the payload does not match."""
    payload_str = json.dumps({"code": "original"}, sort_keys=True)
    approval_id = svc.store_approval("eval", payload=payload_str, requested_by="agent")
    svc.approve_operation(approval_id, approved=True, decided_by="admin")

    tampered = json.dumps({"code": "tampered"}, sort_keys=True)
    assert svc.use_approval(approval_id, tampered) is False


# ---------------------------------------------------------------------------
# 4. Reject blocks further use
# ---------------------------------------------------------------------------

def test_reject_blocks_use(tmp_db, svc):
    """A rejected approval cannot be consumed."""
    payload_str = json.dumps({"code": "z"}, sort_keys=True)
    approval_id = svc.store_approval("eval", payload=payload_str, requested_by="agent")
    svc.approve_operation(approval_id, approved=False, decided_by="admin")
    assert svc.use_approval(approval_id, payload_str) is False


# ---------------------------------------------------------------------------
# 5. Self-approval prevention
# ---------------------------------------------------------------------------

def test_self_approval_blocked(tmp_db, svc):
    """The engine must raise PermissionError when requester == decided_by."""
    approval_id = svc.store_approval("eval", payload="code", requested_by="alice")
    with pytest.raises(PermissionError):
        svc.approve_operation(approval_id, approved=True, decided_by="alice")


# ---------------------------------------------------------------------------
# 6. API-level 409 for already-processed requests
# ---------------------------------------------------------------------------

def test_reject_blocks_approval_at_api(tmp_db):
    """After REJECTED, POST approve must return 409."""
    from fastapi.testclient import TestClient
    from matlab_os.web import api

    req_id = ap.create_approval_request("eval", {"code": "api_test"}, db_path=tmp_db)
    # Reject it via the approvals module (using a different approver to satisfy self-approval check)
    ap.reject_request(req_id, approver="admin", db_path=tmp_db)

    client = TestClient(api.app)
    resp = client.post(f"/api/v1/approvals/{req_id}/approve", json={"approver": "admin"})
    assert resp.status_code == 409, (
        f"Expected 409 for rejected request, got {resp.status_code}: {resp.text}"
    )


def test_unknown_request_returns_none(tmp_db):
    """get_approval_status returns None for an unknown ID."""
    result = ap.get_approval_status("99999", db_path=tmp_db)
    assert result is None
