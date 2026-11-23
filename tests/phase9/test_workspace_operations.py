"""Tests for controlled workspace operations.

The suite proves that every write/creation/deletion path is bound by path
validation, approval, backup and integrity verification, and that every
operation is audited.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from matlab_os.phase9.errors import (
    ApprovalRejectedError,
    ApprovalRequiredError,
    OperationLimitError,
    PathSecurityError,
    PermissionDeniedError,
    WorkspaceError,
)
from matlab_os.phase9.security.approval import ApprovalGate
from matlab_os.phase9.workspace.operations import (
    OperationRecord,
    WorkspaceManager,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _approve_write(
    gate: ApprovalGate,
    relative: str,
    content: str,
    *,
    actor: str = "agent:test",
    approver: str = "human:owner",
    operation: str = "workspace.write",
) -> str:
    """Create and approve a payload-bound write approval, returning its token."""
    binding = {
        "operation": operation,
        "relative": relative,
        "sha256": _sha256(content),
    }
    request = gate.request(operation, relative, binding, actor)
    approval = gate.approve(request.request_id, approver)
    assert approval.token
    return approval.token


# --------------------------------------------------------------------------- #
# Approval enforcement
# --------------------------------------------------------------------------- #
def test_write_requires_approval(strict_workspace: WorkspaceManager):
    with pytest.raises(ApprovalRequiredError):
        strict_workspace.write_text("a.txt", "hello", actor="agent:test")


def test_create_requires_approval(strict_workspace: WorkspaceManager):
    with pytest.raises(ApprovalRequiredError):
        strict_workspace.create_text("a.txt", "hello", actor="agent:test")


def test_write_with_valid_approval_succeeds(
    strict_workspace: WorkspaceManager, approval_gate: ApprovalGate
):
    token = _approve_write(approval_gate, "a.txt", "hello")
    record = strict_workspace.write_text(
        "a.txt", "hello", actor="agent:test", approval_token=token
    )
    assert isinstance(record, OperationRecord)
    assert record.outcome == "success"
    assert record.sha256_after == _sha256("hello")
    assert strict_workspace.read_text("a.txt", actor="agent:test") == "hello"


def test_write_with_mismatched_payload_is_rejected(
    strict_workspace: WorkspaceManager, approval_gate: ApprovalGate
):
    token = _approve_write(approval_gate, "a.txt", "approved content")
    with pytest.raises(ApprovalRejectedError):
        strict_workspace.write_text(
            "a.txt", "different content", actor="agent:test",
            approval_token=token
        )


def test_approval_token_cannot_be_reused(
    strict_workspace: WorkspaceManager, approval_gate: ApprovalGate
):
    token = _approve_write(approval_gate, "a.txt", "hello")
    strict_workspace.write_text(
        "a.txt", "hello", actor="agent:test", approval_token=token
    )
    with pytest.raises(ApprovalRejectedError):
        strict_workspace.write_text(
            "a.txt", "hello", actor="agent:test", approval_token=token
        )


def test_self_approved_write_is_rejected(
    strict_workspace: WorkspaceManager, approval_gate: ApprovalGate
):
    binding = {
        "operation": "workspace.write",
        "relative": "a.txt",
        "sha256": _sha256("hello"),
    }
    request = approval_gate.request(
        "workspace.write", "a.txt", binding, "agent:test"
    )
    with pytest.raises(ApprovalRejectedError):
        approval_gate.approve(request.request_id, "agent:test")


# --------------------------------------------------------------------------- #
# Write / create semantics
# --------------------------------------------------------------------------- #
def test_write_creates_nested_directories(workspace: WorkspaceManager):
    record = workspace.write_text(
        "reports/deep/note.txt", "content\n", actor="agent:test"
    )
    assert record.relative == "reports/deep/note.txt"
    assert workspace.read_text("reports/deep/note.txt", actor="agent:test") == (
        "content\n"
    )


def test_create_refuses_existing_target(workspace: WorkspaceManager):
    workspace.create_text("new.txt", "first", actor="agent:test")
    with pytest.raises(WorkspaceError):
        workspace.create_text("new.txt", "second", actor="agent:test")


def test_write_records_before_and_after_hashes(workspace: WorkspaceManager):
    workspace.write_text("f.txt", "one", actor="agent:test")
    record = workspace.write_text("f.txt", "two", actor="agent:test")
    assert record.sha256_before == _sha256("one")
    assert record.sha256_after == _sha256("two")


def test_overwrite_creates_backup(
    workspace: WorkspaceManager, backups
):
    workspace.write_text("f.txt", "original", actor="agent:test")
    record = workspace.write_text("f.txt", "updated", actor="agent:test")
    assert record.backup_id
    records = backups.list()
    assert any(item.original_relative == "f.txt" for item in records)


def test_backup_content_matches_previous_version(
    workspace: WorkspaceManager, backups, guard
):
    workspace.write_text("f.txt", "original", actor="agent:test")
    workspace.write_text("f.txt", "updated", actor="agent:test")
    record = backups.list()[0]
    restored = backups.restore(record.backup_id)
    assert guard.read_text(restored.relative)[0] == "original"


# --------------------------------------------------------------------------- #
# Path containment
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "candidate",
    ["../escape.txt", "/etc/passwd", "C:/Windows/x", "a/../../b", "CON"],
)
def test_write_rejects_escaping_paths(workspace: WorkspaceManager, candidate):
    with pytest.raises(PathSecurityError):
        workspace.write_text(candidate, "x", actor="agent:test")


def test_read_rejects_traversal(workspace: WorkspaceManager):
    with pytest.raises(PathSecurityError):
        workspace.read_text("../../etc/passwd", actor="agent:test")


def test_reserved_backup_directory_is_not_writable():
    """The sandbox guard installed by the runtime protects .phase9_backups."""
    from matlab_os.phase9.security.paths import PathGuard

    guard = PathGuard(Path.cwd(), reserved_prefixes=(".phase9_backups",))
    with pytest.raises(PathSecurityError):
        guard.resolve(".phase9_backups/manifest.jsonl")


# --------------------------------------------------------------------------- #
# Deletion policy
# --------------------------------------------------------------------------- #
def test_delete_is_disabled_by_default(workspace: WorkspaceManager):
    workspace.write_text("victim.txt", "x", actor="agent:test")
    with pytest.raises(PermissionDeniedError):
        workspace.delete("victim.txt", actor="agent:test")


def test_delete_requires_approval_when_enabled(workspace: WorkspaceManager):
    workspace.write_text("victim.txt", "x", actor="agent:test")
    workspace.config.allow_deletions = True
    with pytest.raises(ApprovalRequiredError):
        workspace.delete("victim.txt", actor="agent:test")


def test_delete_with_approval_removes_file(
    workspace: WorkspaceManager, approval_gate: ApprovalGate
):
    workspace.write_text("victim.txt", "x", actor="agent:test")
    workspace.config.allow_deletions = True
    token = _approve_write(
        approval_gate, "victim.txt", "x", operation="workspace.delete"
    )
    record = workspace.delete(
        "victim.txt", actor="agent:test", approval_token=token
    )
    assert record.kind == "delete"
    assert workspace.stat("victim.txt")["exists"] is False


# --------------------------------------------------------------------------- #
# Integrity / manifests
# --------------------------------------------------------------------------- #
def test_verify_integrity_true_for_matching_content(workspace: WorkspaceManager):
    workspace.write_text("f.txt", "payload", actor="agent:test")
    assert workspace.verify_integrity(
        "f.txt", _sha256("payload"), actor="agent:test"
    )


def test_verify_integrity_false_for_mismatch(workspace: WorkspaceManager):
    workspace.write_text("f.txt", "payload", actor="agent:test")
    assert not workspace.verify_integrity(
        "f.txt", _sha256("other"), actor="agent:test"
    )


def test_tree_manifest_hashes_every_file(workspace: WorkspaceManager):
    workspace.write_text("a/one.txt", "1", actor="agent:test")
    workspace.write_text("a/two.txt", "22", actor="agent:test")
    manifest = workspace.tree_manifest("a", actor="agent:test")
    assert manifest["file_count"] == 2
    assert manifest["files"]["a/one.txt"]["sha256"] == _sha256("1")


def test_list_dir_reports_entries(workspace: WorkspaceManager):
    workspace.write_text("dir/file.txt", "x", actor="agent:test")
    entries = workspace.list_dir("dir", actor="agent:test")
    assert [entry["name"] for entry in entries] == ["file.txt"]
    assert entries[0]["is_file"] is True


def test_stat_reports_absence(workspace: WorkspaceManager):
    assert workspace.stat("missing.txt")["exists"] is False


# --------------------------------------------------------------------------- #
# Budgets and audit
# --------------------------------------------------------------------------- #
def test_operation_budget_is_enforced(config, guard, approval_gate, audit):
    config.security.max_operations_per_task = 2
    manager = WorkspaceManager(
        guard,
        config.security,
        approval_gate=approval_gate,
        audit=audit,
        backups=None,
    )
    manager.write_text("a.txt", "1", actor="agent:test")
    manager.write_text("b.txt", "2", actor="agent:test")
    with pytest.raises(OperationLimitError):
        manager.write_text("c.txt", "3", actor="agent:test")


def test_operations_are_audited(workspace: WorkspaceManager, audit):
    workspace.write_text("f.txt", "x", actor="agent:test")
    actions = audit.summary()["actions"]
    assert "workspace.write" in actions
    assert audit.verify_chain()["valid"] is True


def test_refused_write_is_audited(strict_workspace: WorkspaceManager, audit):
    with pytest.raises(ApprovalRequiredError):
        strict_workspace.write_text("f.txt", "x", actor="agent:test")
    assert "workspace.approval_required" in audit.summary()["actions"]


def test_actor_is_required(workspace: WorkspaceManager):
    with pytest.raises(ValueError):
        workspace.write_text("f.txt", "x", actor="")


def test_operation_record_serialises(workspace: WorkspaceManager):
    record = workspace.write_text("f.txt", "x", actor="agent:test")
    payload = record.to_dict()
    assert json.dumps(payload)
    assert payload["actor"] == "agent:test"
