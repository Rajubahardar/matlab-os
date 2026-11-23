"""Approval gate tests. Written BEFORE the fix: all 7 must fail today.

Contract the implementation must satisfy (Task 2):
  store_approval(operation, payload="", requested_by=None, reason=None)
      -> always creates status "pending"; any other status raises ValueError
  approve_operation(approval_id, approved, decided_by, reason=None)
      -> "approved" has no default; the requester cannot decide their own request
         (PermissionError); sets status to "approved" or "rejected"
  use_approval(approval_id, payload, now=None) -> bool
      -> True once, only if approved, unexpired (15 min), unused, hash matches
"""
from datetime import datetime, timedelta, timezone

import pytest

from matlab_os.memory.database import DatabaseService

SCRIPT = "x = 1;"


@pytest.fixture
def db(tmp_path):
    service = DatabaseService(db_path=str(tmp_path / "test.db"))
    yield service
    service.close()


def _request(db, payload=SCRIPT, requested_by="agent"):
    return db.store_approval("run_matlab", payload=payload,
                             requested_by=requested_by)


def _approved(db, payload=SCRIPT):
    aid = _request(db, payload)
    db.approve_operation(aid, approved=True, decided_by="user")
    return aid


def test_decision_must_be_explicit(db):
    aid = db.store_approval("run_matlab", requested_by="agent")
    with pytest.raises(TypeError):
        db.approve_operation(aid)


def test_approval_bound_to_payload_hash(db):
    aid = _approved(db, payload=SCRIPT)
    assert db.use_approval(aid, payload="delete(myfile);") is False
    assert db.use_approval(aid, payload=SCRIPT) is True


def test_approval_is_single_use(db):
    aid = _approved(db)
    assert db.use_approval(aid, payload=SCRIPT) is True
    assert db.use_approval(aid, payload=SCRIPT) is False


def test_approval_expires_after_15_minutes(db):
    now = datetime.now(timezone.utc)
    early = _approved(db)
    late = _approved(db)
    assert db.use_approval(early, SCRIPT, now=now + timedelta(minutes=14)) is True
    assert db.use_approval(late, SCRIPT, now=now + timedelta(minutes=16)) is False


def test_requester_cannot_approve_own_request(db):
    aid = _request(db, requested_by="agent")
    with pytest.raises(PermissionError):
        db.approve_operation(aid, approved=True, decided_by="agent")


def test_cannot_create_preapproved_request(db):
    with pytest.raises(ValueError):
        db.store_approval("run_matlab", status="approved", requested_by="agent")


def test_default_deny_when_pending_or_rejected(db):
    pending = _request(db)
    assert db.use_approval(pending, payload=SCRIPT) is False
    rejected = _request(db)
    db.approve_operation(rejected, approved=False, decided_by="user")
    assert db.use_approval(rejected, payload=SCRIPT) is False