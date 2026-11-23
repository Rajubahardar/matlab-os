# -*- coding: utf-8 -*-
"""Approval engine facade for Local AI OS.

The canonical approval implementation lives in
``matlab_os.memory.database.DatabaseService``.  That class provides the
complete approval lifecycle:

* SHA-256 payload binding
* 15-minute TTL (enforced at ``use_approval`` time)
* One-use semantics (status transitions pending → approved/rejected → used)
* Self-approval prevention

This module re-exports the canonical functions through a stable public API so
the FastAPI routes and other callers can import from one place without coupling
to the memory layer directly.

The ``DEFAULT_DB`` path is shared with the agent's ``DatabaseService``
instance so both access the same SQLite file.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from matlab_os.memory.database import DatabaseService, APPROVAL_TTL

# Default path for the shared database (same file used by MATLABAgent).
DEFAULT_DB: Path = Path(__file__).resolve().parents[2] / "data" / "matlab_agent.db"


# ---------------------------------------------------------------------------
# Module-level DatabaseService singleton (created lazily, path-keyed)
# ---------------------------------------------------------------------------

_svc_cache: dict[str, DatabaseService] = {}


def _get_service(db_path: str | Path = DEFAULT_DB) -> DatabaseService:
    """Return (or create) a ``DatabaseService`` bound to *db_path*."""
    key = str(Path(db_path).resolve())
    if key not in _svc_cache:
        _svc_cache[key] = DatabaseService(db_path=str(db_path), check_same_thread=False)
    return _svc_cache[key]


def close_services() -> None:
    """Close and clear all cached DatabaseService connections."""
    import gc
    for svc in list(_svc_cache.values()):
        try:
            svc.close()
        except Exception:
            pass
    _svc_cache.clear()
    gc.collect()


# ---------------------------------------------------------------------------
# Public API — compatible signatures with both the FastAPI layer and tests
# ---------------------------------------------------------------------------

def create_approval_request(
    operation: str,
    payload: Dict[str, Any],
    payload_hash: Optional[str] = None,
    *,
    db_path: str | Path | None = None,
    creator: str = "agent",
) -> str:
    """Create a new PENDING approval request via the canonical engine.

    Returns a string representation of the integer approval ID so callers
    that expect a string ID (e.g. the FastAPI routes and their tests) work
    unchanged.

    ``payload_hash`` is accepted for API compatibility but is computed
    internally by ``DatabaseService``; if supplied it is not used to override
    the canonical computation.
    """
    if db_path is None:
        db_path = DEFAULT_DB
    # Serialise payload to a canonical string the way DatabaseService expects.
    payload_str = json.dumps(payload, sort_keys=True)
    svc = _get_service(db_path)
    approval_id = svc.store_approval(
        operation=operation,
        payload=payload_str,
        requested_by=creator,
        reason=f"API request for operation: {operation}",
    )
    return str(approval_id)


def get_pending_approvals(db_path: str | Path | None = None) -> List[Dict[str, Any]]:
    """Return all non-expired PENDING approval requests.

    Each dict contains the keys expected by the FastAPI ``ApprovalInfo``
    schema: ``request_id``, ``operation``, ``payload_sha256``, ``expires_at``.
    """
    from datetime import datetime, timezone

    if db_path is None:
        db_path = DEFAULT_DB

    svc = _get_service(db_path)
    cur = svc.conn.cursor()
    rows = cur.execute(
        "SELECT id, operation, payload_sha256, requested_at "
        "FROM approvals WHERE status='pending'"
    ).fetchall()
    result = []
    now = datetime.now(timezone.utc)
    for row in rows:
        req_id, operation, sha256, requested_at = row
        if requested_at:
            try:
                created = datetime.fromisoformat(requested_at)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                expires = created + APPROVAL_TTL
                if expires <= now:
                    continue  # expired — skip
                expires_ts = int(expires.timestamp())
            except (ValueError, OSError):
                expires_ts = 0
        else:
            expires_ts = 0
        result.append(
            {
                "request_id": str(req_id),
                "operation": operation,
                "payload_sha256": sha256 or "",
                "expires_at": expires_ts,
            }
        )
    return result


def approve_request(
    request_id: str,
    *,
    approver: str = "admin",
    db_path: str | Path | None = None,
) -> None:
    """Approve a PENDING request using the canonical engine.

    ``approver`` must differ from the original requester or the engine will
    raise ``PermissionError`` (self-approval prevention).
    """
    if db_path is None:
        db_path = DEFAULT_DB
    svc = _get_service(db_path)
    svc.approve_operation(
        approval_id=int(request_id),
        approved=True,
        decided_by=approver,
        reason="Approved via API",
    )


def reject_request(
    request_id: str,
    *,
    approver: str = "admin",
    db_path: str | Path | None = None,
) -> None:
    """Reject a PENDING request using the canonical engine."""
    if db_path is None:
        db_path = DEFAULT_DB
    svc = _get_service(db_path)
    svc.approve_operation(
        approval_id=int(request_id),
        approved=False,
        decided_by=approver,
        reason="Rejected via API",
    )


def get_approval_status(
    request_id: str,
    db_path: str | Path | None = None,
) -> Optional[str]:
    """Return the current status string for *request_id*, or ``None``."""
    try:
        req_int = int(request_id)
    except ValueError:
        return None

    if db_path is None:
        db_path = DEFAULT_DB
    svc = _get_service(db_path)
    cur = svc.conn.cursor()
    row = cur.execute(
        "SELECT status FROM approvals WHERE id=?", (req_int,)
    ).fetchone()
    if row is None:
        return None
    # Normalise to the uppercase strings the FastAPI layer checks.
    return row[0].upper() if row[0] else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_db(db_path: str | Path) -> Path:
    """Resolve the database path, respecting module-level ``DEFAULT_DB``."""
    p = Path(db_path)
    if p == DEFAULT_DB:
        return DEFAULT_DB
    return p.resolve()


# Public API surface
__all__ = [
    "DEFAULT_DB",
    "create_approval_request",
    "get_pending_approvals",
    "approve_request",
    "reject_request",
    "get_approval_status",
    "close_services",
]
