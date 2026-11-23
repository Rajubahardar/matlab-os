"""Controlled workspace operations for Phase 9 (staging only)."""




from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

from ..audit import AuditLog, NullAuditLog
from ..config import SecurityConfig
from ..errors import (
    ApprovalRequiredError,
    OperationLimitError,
    PermissionDeniedError,
    WorkspaceError,
)
from ..security.approval import ApprovalGate
from ..security.paths import PathGuard, ResolvedPath
from .backup import BackupStore

#: Approval operation scopes used by this module.
WRITE_OPERATION = "workspace.write"
CREATE_OPERATION = "workspace.create"
DELETE_OPERATION = "workspace.delete"

#: Upper bound for a single tree walk, independent of the per-file size limit.
MAX_MANIFEST_FILES = 10_000


def _text_digest(text: str) -> str:
    """SHA-256 of the exact bytes this module will write for *text*."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalise_newlines(text: str) -> str:
    """Normalise CRLF/CR to LF so hashes are platform independent."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class OperationRecord:
    """One attempted workspace mutation, successful or not."""

    operation_id: str
    kind: str
    relative: str
    actor: str
    outcome: str
    sha256_before: Optional[str] = None
    sha256_after: Optional[str] = None
    size: int = 0
    backup_id: Optional[str] = None
    approval_request_id: Optional[str] = None
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Return a serialisable, secret-free description."""
        return {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "relative": self.relative,
            "actor": self.actor,
            "outcome": self.outcome,
            "sha256_before": self.sha256_before,
            "sha256_after": self.sha256_after,
            "size": self.size,
            "backup_id": self.backup_id,
            "approval_request_id": self.approval_request_id,
            "timestamp": self.timestamp,
        }


class WorkspaceManager:
    """Validated, approved, backed-up and audited workspace access."""

    def __init__(
        self,
        guard: PathGuard,
        config: SecurityConfig,
        *,
        approval_gate: ApprovalGate,
        audit: Optional[AuditLog] = None,
        backups: Optional[BackupStore] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(guard, PathGuard):
            raise TypeError("guard must be a PathGuard instance.")
        if not isinstance(config, SecurityConfig):
            raise TypeError("config must be a SecurityConfig instance.")
        if not isinstance(approval_gate, ApprovalGate):
            raise TypeError("approval_gate must be an ApprovalGate instance.")
        if not callable(clock):
            raise TypeError("clock must be callable.")

        self._guard = guard
        self._config = config
        self._approvals = approval_gate
        self._audit_log = audit or NullAuditLog()
        self._backups = backups if config.backups_enabled else None
        self._clock = clock
        self._lock = threading.RLock()
        self._records: list[OperationRecord] = []

    # -- introspection ----------------------------------------------------- #
    @property
    def guard(self) -> PathGuard:
        """The path guard used for every filesystem access."""
        return self._guard

    @property
    def config(self) -> SecurityConfig:
        """The live security policy (tests may tighten it per instance)."""
        return self._config

    @property
    def approval_gate(self) -> ApprovalGate:
        return self._approvals

    @property
    def backups(self) -> Optional[BackupStore]:
        return self._backups

    def operations(self) -> list[OperationRecord]:
        """Return every recorded mutation attempt, oldest first."""
        with self._lock:
            return list(self._records)

    def status(self) -> dict[str, Any]:
        """Return a compact, secret-free status description."""
        with self._lock:
            count = len(self._records)
            last = self._records[-1].to_dict() if self._records else None
        return {
            "workspace_root": str(self._guard.root),
            "operations": count,
            "max_operations_per_task": self._config.max_operations_per_task,
            "deletions_allowed": bool(self._config.allow_deletions),
            "approval_required_for_writes": bool(
                self._config.require_approval_for_writes
            ),
            "approval_required_for_deletion": bool(
                self._config.require_approval_for_deletion
            ),
            "backups_enabled": self._backups is not None,
            "last_operation": last,
        }

    # -- reads ------------------------------------------------------------- #
    def read_text(self, relative: str, *, actor: str) -> str:
        """Read a validated UTF-8 file as text."""
        self._require_actor(actor)
        self._require_mode("read")
        text, _digest = self._guard.read_text(relative)
        self._audit("workspace.read", "success", {
            "relative": relative,
            "actor": actor,
            "size": len(text),
        })
        return text

    def stat(self, relative: str) -> dict[str, Any]:
        """Describe a workspace path without creating or modifying it."""
        self._require_mode("stat")
        resolved = self._guard.resolve(relative)
        return {
            "relative": resolved.relative,
            "exists": resolved.exists,
            "is_file": resolved.is_file,
            "is_dir": resolved.is_dir,
            "size": resolved.size,
        }

    def list_dir(self, relative: str, *, actor: str) -> list[dict[str, Any]]:
        """List one workspace directory, sorted by name."""
        self._require_actor(actor)
        resolved = self._guard.resolve(relative, want="dir")

        entries: list[dict[str, Any]] = []
        try:
            names = sorted(os.listdir(os.fspath(resolved.absolute)))
        except OSError as exc:
            raise WorkspaceError(
                "Directory could not be listed.",
                path=resolved.relative,
                error=exc.strerror or "unknown",
            ) from exc

        for name in names:
            child = self._guard.resolve(f"{resolved.relative}/{name}")
            entries.append(
                {
                    "name": name,
                    "relative": child.relative,
                    "is_file": child.is_file,
                    "is_dir": child.is_dir,
                    "size": child.size,
                }
            )
        return entries

    # -- writes ------------------------------------------------------------ #

    def write_text(
        self,
        relative: str,
        text: str,
        *,
        actor: str,
        approval_token: Optional[str] = None,
        reason: str = "",
        overwrite: bool = True,
    ) -> OperationRecord:
        """Write text into the workspace under every Phase 9 control."""
        return self._write(
            relative,
            text,
            actor=actor,
            approval_token=approval_token,
            reason=reason,
            overwrite=overwrite,
            operation=WRITE_OPERATION,
        )

    def create_text(
        self,
        relative: str,
        text: str,
        *,
        actor: str,
        approval_token: Optional[str] = None,
        reason: str = "",
    ) -> OperationRecord:
        """Create a new file, refusing to replace an existing one."""
        return self._write(
            relative,
            text,
            actor=actor,
            approval_token=approval_token,
            reason=reason,
            overwrite=False,
            operation=CREATE_OPERATION,
        )

    def delete(
        self,
        relative: str,
        *,
        actor: str,
        approval_token: Optional[str] = None,
    ) -> OperationRecord:
        """Delete one regular file, when policy and approval both allow it."""
        self._require_actor(actor)
        self._require_mode("delete")

        if not self._config.allow_deletions:
            self._audit(DELETE_OPERATION, "denied", {
                "relative": relative,
                "actor": actor,
                "reason": "deletions_disabled",
            })
            raise PermissionDeniedError(
                "Deletion is disabled by configuration.",
                relative=relative,
            )
    def list_dir(self, relative: str, *, actor: str) -> list[dict[str, Any]]:
        """List one workspace directory, sorted by name."""
        self._require_actor(actor)
        resolved = self._guard.resolve(relative, want="dir")

        entries: list[dict[str, Any]] = []
        try:
            names = sorted(os.listdir(os.fspath(resolved.absolute)))
        except OSError as exc:
            raise WorkspaceError(
                "Directory could not be listed.",
                path=resolved.relative,
                error=exc.strerror or "unknown",
            ) from exc

        for name in names:
            child = self._guard.resolve(f"{resolved.relative}/{name}")
            entries.append(
                {
                    "name": name,
                    "relative": child.relative,
                    "is_file": child.is_file,
                    "is_dir": child.is_dir,
                    "size": child.size,
                }
            )
        return entries

    # -- writes ------------------------------------------------------------ #
    def write_text(
        self,
        relative: str,
        text: str,
        *,
        actor: str,
        approval_token: Optional[str] = None,
        reason: str = "",
        overwrite: bool = True,
    ) -> OperationRecord:
        """Write text into the workspace under every Phase 9 control."""
        return self._write(
            relative,
            text,
            actor=actor,
            approval_token=approval_token,
            reason=reason,
            overwrite=overwrite,
            operation=WRITE_OPERATION,
        )

    def create_text(
        self,
        relative: str,
        text: str,
        *,
        actor: str,
        approval_token: Optional[str] = None,
        reason: str = "",
    ) -> OperationRecord:
        """Create a new file, refusing to replace an existing one."""
        return self._write(
            relative,
            text,
            actor=actor,
            approval_token=approval_token,
            reason=reason,
            overwrite=False,
            operation=CREATE_OPERATION,
        )

    def _write(
        self,
        relative: str,
        text: str,
        *,
        actor: str,
        approval_token: Optional[str],
        reason: str,
        overwrite: bool,
        operation: str,
    ) -> OperationRecord:
        """Shared implementation for ``write_text`` and ``create_text``."""
        self._require_text(text)
        self._require_actor(actor)
        self._consume_budget()

        payload = _normalise_newlines(text)
        digest = _text_digest(payload)
        size = len(payload.encode("utf-8"))

        target = self._guard.resolve(relative)
        before = self._read_existing(target)

        approved_request = self._authorise(
            operation,
            target.relative,
            digest,
            approval_token,
            actor,
            required=self._config.require_approval_for_writes,
        )

        if target.exists and not overwrite:
            self._audit("workspace.create", "refused", {
                "relative": target.relative,
                "actor": actor,
                "reason": "target_exists",
            })
            raise WorkspaceError(
                "Target already exists and overwrite is disabled.",
                path=target.relative,
            )

        backup_id = self._backup_before_write(target) if before else None

        resolved, written_digest = self._guard.atomic_write_text(
            target.relative, payload
        )
        if written_digest != digest:
            raise WorkspaceError(
                "Written content digest does not match the approved content.",
                path=resolved.relative,
                expected=digest,
                actual=written_digest,
            )

        record = self._record(
            kind="write" if before else "create",
            relative=resolved.relative,
            actor=actor,
            outcome="success",
            sha256_before=before[1] if before else None,
            sha256_after=written_digest,
            size=size,
            backup_id=backup_id,
            approval_request_id=approved_request,
        )
        self._audit(
            operation,
            "success",
            {
                "relative": record.relative,
                "actor": actor,
                "size": size,
                "sha256": written_digest,
                "sha256_before": record.sha256_before,
                "backup_id": backup_id,
                "reason": reason,
                "approval_request_id": approved_request,
            },
        )
        return record

    def delete(
        self,
        relative: str,
        *,
        actor: str,
        approval_token: Optional[str] = None,
    ) -> OperationRecord:
        """Delete one regular file, when policy and approval both allow it."""
        self._require_actor(actor)
        self._require_mode("delete")

        if not self._config.allow_deletions:
            self._audit(DELETE_OPERATION, "denied", {
                "relative": relative,
                "actor": actor,
                "reason": "deletions_disabled",
            })
            raise PermissionDeniedError(
                "Deletion is disabled by configuration.",
                relative=relative,
            )

        self._consume_budget()

        target = self._guard.resolve(relative, want="file")
        before = self._read_existing(target)
        existing_digest = before[1] if before else None

        # Deletion always requires an explicit human approval once enabled:
        # ``allow_deletions`` widens what is *possible*, never what is
        # *permitted without consent*.
        approved_request = self._authorise(
            DELETE_OPERATION,
            target.relative,
            existing_digest,
            approval_token,
            actor,
            required=True,
        )

        removed = self._guard.remove_file(target.relative)

        record = self._record(
            kind="delete",
            relative=removed,
            actor=actor,
            outcome="success",
            sha256_before=existing_digest,
            sha256_after=None,
            size=before[2] if before else 0,
            backup_id=None,
            approval_request_id=approved_request,
        )
        self._audit(DELETE_OPERATION, "success", {
            "relative": removed,
            "actor": actor,
            "sha256_before": existing_digest,
            "approval_request_id": approved_request,
        })
        return record

    # -- integrity --------------------------------------------------------- #
    def verify_integrity(
        self, relative: str, expected_sha256: str, *, actor: str
    ) -> bool:
        """Return True when the file's SHA-256 matches *expected_sha256*."""
        self._require_actor(actor)
        if not isinstance(expected_sha256, str) or not expected_sha256.strip():
            raise ValueError("expected_sha256 must be a non-empty string.")

        try:
            _data, digest = self._guard.read_bytes(relative)
        except WorkspaceError:
            # A missing or unreadable file cannot be verified; that is a
            # negative result for a probe, not a security downgrade.
            self._audit("workspace.verify", "missing", {
                "relative": relative,
                "actor": actor,
            })
            return False

        matched = digest == expected_sha256.strip().lower()
        self._audit(
            "workspace.verify",
            "match" if matched else "mismatch",
            {
                "relative": relative,
                "actor": actor,
                "expected": expected_sha256.strip().lower(),
                "actual": digest,
            },
        )
        return matched

    def tree_manifest(
        self, relative: str = ".", *, actor: str
    ) -> dict[str, Any]:
        """Hash every file under *relative* and return a manifest."""
        self._require_actor(actor)

        root = self._manifest_root(relative)
        files: dict[str, dict[str, Any]] = {}
        total_bytes = 0

        for directory, _subdirs, names in os.walk(
            os.fspath(root.absolute), followlinks=False
        ):
            for name in sorted(names):
                absolute = os.path.join(directory, name)
                child_relative = os.path.relpath(
                    absolute, os.fspath(self._guard.root)
                ).replace("\\", "/")
                # Validate through the guard: symlinks, reparse points,
                # oversized files and escaping paths are rejected here.
                resolved = self._guard.resolve(child_relative, want="file")
                data, digest = self._guard.read_bytes(resolved.relative)
                files[resolved.relative] = {
                    "sha256": digest,
                    "size": len(data),
                }
                total_bytes += len(data)
                if len(files) > MAX_MANIFEST_FILES:
                    raise OperationLimitError(
                        "Tree manifest exceeds the file-count limit.",
                        path=root.relative,
                        limit=MAX_MANIFEST_FILES,
                    )

        manifest = {
            "root": root.relative,
            "file_count": len(files),
            "total_bytes": total_bytes,
            "files": files,
            "generated_at": self._clock(),
        }
        self._audit("workspace.manifest", "success", {
            "root": root.relative,
            "actor": actor,
            "file_count": len(files),
            "total_bytes": total_bytes,
        })
        return manifest

    # -- internals --------------------------------------------------------- #
    def _manifest_root(self, relative: str) -> ResolvedPath:
        if not isinstance(relative, str):
            raise ValueError("relative must be a string.")
        normalised = relative.strip().replace("\\", "/")
        if normalised in ("", ".", "./", "/"):
            # The guard deliberately rejects the root itself; walk it directly.
            return ResolvedPath(
                absolute=self._guard.root,
                relative=".",
                exists=True,
                is_file=False,
                is_dir=True,
                size=None,
                validated_at=self._clock(),
            )
        return self._guard.resolve(normalised, want="dir")

    def _read_existing(
        self, target: ResolvedPath
    ) -> Optional[tuple[bytes, str, int]]:
        """Return ``(bytes, sha256, size)`` for an existing regular file."""
        if not target.exists:
            return None
        if not target.is_file:
            raise WorkspaceError(
                "Target exists and is not a regular file.",
                path=target.relative,
            )
        data, digest = self._guard.read_bytes(target.relative)
        return data, digest, len(data)

    def _authorise(
        self,
        operation: str,
        relative: str,
        content_sha256: Optional[str],
        token: Optional[str],
        actor: str,
        *,
        required: bool,
    ) -> Optional[str]:
        """Verify and consume an approval token, or refuse the operation."""
        if token is None:
            if not required:
                return None
            self._audit("workspace.approval_required", "denied", {
                "operation": operation,
                "relative": relative,
                "actor": actor,
            })
            raise ApprovalRequiredError(
                "This workspace operation requires an explicit approval.",
                operation=operation,
                relative=relative,
            )

        # The approval is bound to the operation, the target path and the
        # exact pre-existing file content (its SHA-256).  Pass the same
        # payload shape at verify time that was used at request time.
        approval = self._approvals.verify(
            token,
            operation,
            relative,
            {"sha256": content_sha256},
            consumer=actor,
        )
        return approval.request_id

    def _backup_before_write(self, target: ResolvedPath) -> Optional[str]:
        if self._backups is None or not target.is_file:
            return None
        record = self._backups.create(
            target.relative, reason="pre-write backup"
        )
        return record.backup_id

    def _consume_budget(self) -> None:
        """Enforce the per-task mutation budget before doing any work."""
        limit = int(self._config.max_operations_per_task)
        with self._lock:
            used = len(self._records)
        if used >= limit:
            self._audit("workspace.budget_exhausted", "denied", {
                "used": used,
                "limit": limit,
            })
            raise OperationLimitError(
                "The per-task workspace operation budget is exhausted.",
                used=used,
                limit=limit,
            )

    def _record(
        self,
        *,
        kind: str,
        relative: str,
        actor: str,
        outcome: str,
        sha256_before: Optional[str],
        sha256_after: Optional[str],
        size: int,
        backup_id: Optional[str],
        approval_request_id: Optional[str],
    ) -> OperationRecord:
        record = OperationRecord(
            operation_id=uuid.uuid4().hex,
            kind=kind,
            relative=relative,
            actor=actor,
            outcome=outcome,
            sha256_before=sha256_before,
            sha256_after=sha256_after,
            size=size,
            backup_id=backup_id,
            approval_request_id=approval_request_id,
            timestamp=self._clock(),
        )
        with self._lock:
            self._records.append(record)
        return record

    def _audit(
        self, action: str, outcome: str, details: Mapping[str, Any]
    ) -> None:
        self._audit_log.record(
            action=action,
            actor=str(details.get("actor") or "workspace"),
            outcome=outcome,
            details=dict(details),
        )

    def _require_mode(self, mode: str) -> None:
        if not isinstance(mode, str) or not mode.strip():
            raise ValueError("mode must be a non-empty identity.")

    def _require_mode(self, mode: str) -> None:
        if not isinstance(mode, str) or not mode.strip():
            raise ValueError("mode must be a non-empty identity.")

    @staticmethod
    def _require_actor(actor: str) -> None:
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty identity.")

    @staticmethod
    def _require_text(text: str) -> None:
        if not isinstance(text, str):
            raise TypeError("text must be a string.")


__all__ = [
    "DELETE_OPERATION",
    "CREATE_OPERATION",
    "WRITE_OPERATION",
    "MAX_MANIFEST_FILES",
    "OperationRecord",
    "WorkspaceManager",
]
