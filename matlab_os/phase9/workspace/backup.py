"""Versioned workspace backups with SHA-256 verification (Phase 9 staging).

A :class:`BackupStore` keeps point-in-time copies of workspace files inside the
workspace itself, records every copy in an append-only JSONL manifest and can
restore or verify a copy later.

Layout
------
::

    <guard.root>/<directory>/<backup_id>/<original relative path>
    <guard.root>/<directory>/manifest.jsonl

``directory`` defaults to ``.phase9_backups``.  The manifest is one JSON object
per line; :meth:`BackupStore.create` appends a line and the write is made
durable by :meth:`PathGuard.atomic_write_bytes`, which ``fsync``\\ s the
temporary file before replacing the manifest atomically (existing lines are
preserved byte for byte, which gives append semantics through the only write
primitive :class:`PathGuard` exposes).

Security rules
--------------
* Every path is resolved, read and written through
  :class:`matlab_os.phase9.security.paths.PathGuard`.  A traversal attempt
  in ``relative`` is therefore rejected by the guard before any byte is
  touched; this module never re-implements path validation.
* The copy is read back and its SHA-256 compared with the source before the
  manifest records it, so a truncated or rewritten copy can never be recorded
  as a valid backup.
* ``restore`` re-verifies the stored hash before writing, and re-verifies the
  bytes it wrote, before returning the :class:`ResolvedPath`.
* ``verify`` returns ``False`` for a mismatch (it is a probe); ``restore``
  raises :class:`IntegrityError` (it is a mutation).
* Retention pruning deletes the oldest copies first and never the newest one.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..errors import (
    BackupError,
    IntegrityError,
    OperationLimitError,
    PathSecurityError,
    WorkspaceError,
)
from ..security.paths import PathGuard, ResolvedPath

#: Default backup directory, relative to the workspace root.
DEFAULT_DIRECTORY = ".phase9_backups"
#: Manifest file name inside the backup directory.
MANIFEST_NAME = "manifest.jsonl"

_MANIFEST_FIELDS = (
    "backup_id",
    "original_relative",
    "backup_relative",
    "sha256",
    "size",
    "created_at",
    "reason",
)


@dataclass(frozen=True)
class BackupRecord:
    """One recorded backup of one workspace file."""

    backup_id: str
    original_relative: str
    backup_relative: str
    sha256: str
    size: int
    created_at: float
    reason: str

    def describe(self) -> dict[str, Any]:
        """Return a serialisable, secret-free description."""
        return {
            "backup_id": self.backup_id,
            "original_relative": self.original_relative,
            "backup_relative": self.backup_relative,
            "sha256": self.sha256,
            "size": self.size,
            "created_at": self.created_at,
            "reason": self.reason,
        }


def _record_payload(record: BackupRecord) -> dict[str, Any]:
    return {
        "backup_id": record.backup_id,
        "original_relative": record.original_relative,
        "backup_relative": record.backup_relative,
        "sha256": record.sha256,
        "size": record.size,
        "created_at": record.created_at,
        "reason": record.reason,
    }


def _record_from_payload(payload: Any, line_number: int) -> BackupRecord:
    """Rebuild a validated record from one manifest line."""
    if not isinstance(payload, dict):
        raise BackupError(
            "The backup manifest contains a malformed record.",
            line=line_number,
        )
    missing = [field for field in _MANIFEST_FIELDS if field not in payload]
    if missing:
        raise BackupError(
            "The backup manifest record is incomplete.",
            line=line_number,
            missing=", ".join(missing),
        )

    backup_id = payload["backup_id"]
    original_relative = payload["original_relative"]
    backup_relative = payload["backup_relative"]
    sha256 = payload["sha256"]
    size = payload["size"]
    created_at = payload["created_at"]
    reason = payload["reason"]

    valid = (
        isinstance(backup_id, str)
        and bool(backup_id)
        and isinstance(original_relative, str)
        and bool(original_relative)
        and isinstance(backup_relative, str)
        and bool(backup_relative)
        and isinstance(sha256, str)
        and len(sha256) == 64
        and isinstance(size, int)
        and not isinstance(size, bool)
        and size >= 0
        and isinstance(created_at, (int, float))
        and not isinstance(created_at, bool)
        and isinstance(reason, str)
    )
    if not valid:
        raise BackupError(
            "The backup manifest record has invalid field types.",
            line=line_number,
        )

    return BackupRecord(
        backup_id=backup_id,
        original_relative=original_relative,
        backup_relative=backup_relative,
        sha256=sha256,
        size=int(size),
        created_at=float(created_at),
        reason=reason,
    )


class BackupStore:
    """Create, list, verify, restore and prune workspace file backups."""

    def __init__(
        self,
        guard: PathGuard,
        *,
        directory: str = DEFAULT_DIRECTORY,
        retention: int = 50,
        audit: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(guard, PathGuard):
            raise TypeError("guard must be a PathGuard instance.")
        if not isinstance(directory, str) or not directory.strip():
            raise ValueError("directory must be a non-empty string.")
        if isinstance(retention, bool) or not isinstance(retention, int):
            raise ValueError("retention must be an integer.")
        if retention < 0:
            raise ValueError("retention cannot be negative.")
        if not callable(clock):
            raise TypeError("clock must be callable.")

        self._guard = guard
        self._clock = clock
        self._audit_log = audit
        self._retention = int(retention)
        self._lock = threading.RLock()

        resolved_directory = guard.resolve(directory)
        if resolved_directory.exists and not resolved_directory.is_dir:
            raise ValueError(
                "The backup directory exists and is not a directory."
            )
        self._directory = resolved_directory.relative
        self._manifest_relative = f"{self._directory}/{MANIFEST_NAME}"

    # -- introspection ----------------------------------------------------- #
    @property
    def guard(self) -> PathGuard:
        """The path guard used for every filesystem access."""
        return self._guard

    @property
    def directory(self) -> str:
        """The validated, workspace-relative backup directory."""
        return self._directory

    @property
    def manifest_relative(self) -> str:
        """The validated, workspace-relative manifest path."""
        return self._manifest_relative

    @property
    def retention(self) -> int:
        """Configured retention; ``0`` still keeps the newest backup."""
        return self._retention

    # -- create ------------------------------------------------------------ #
    def create(self, relative: str, *, reason: str = "") -> BackupRecord:
        """Back up an existing workspace file and record it.

        ``relative`` must identify an existing regular file inside the
        workspace; the copy's SHA-256 is compared with the source before the
        record is written.  Retention pruning runs afterwards.
        """
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError("relative must be a non-empty string.")
        if not isinstance(reason, str):
            raise TypeError("reason must be a string.")

        with self._lock:
            source = self._guard.resolve(relative, want="file")
            data, source_digest = self._guard.read_bytes(source.relative)
            size = len(data)

            backup_id = uuid.uuid4().hex
            created_at = self._clock()
            backup_relative = (
                f"{self._directory}/{backup_id}/{source.relative}"
            )

            self._guard.atomic_write_bytes(backup_relative, data)

            try:
                stored, stored_digest = self._guard.read_bytes(backup_relative)
            except WorkspaceError as exc:
                self._discard(backup_id)
                raise BackupError(
                    "The backup copy could not be read back for verification.",
                    backup_id=backup_id,
                ) from exc
            if stored_digest != source_digest or stored != data:
                self._discard(backup_id)
                raise IntegrityError(
                    "The backup copy does not match the source content.",
                    backup_id=backup_id,
                    expected=source_digest,
                    actual=stored_digest,
                )

            record = BackupRecord(
                backup_id=backup_id,
                original_relative=source.relative,
                backup_relative=backup_relative,
                sha256=source_digest,
                size=size,
                created_at=float(created_at),
                reason=reason,
            )

            try:
                self._append_manifest(record)
            except BackupError:
                self._discard(backup_id)
                raise

            self._audit_event(
                "success",
                {
                    "operation": "create",
                    "backup_id": record.backup_id,
                    "original_relative": record.original_relative,
                    "sha256": record.sha256,
                    "size": record.size,
                    "reason": record.reason,
                },
            )

            self.prune()
            return record

    # -- read paths -------------------------------------------------------- #
    def list(self) -> list[BackupRecord]:
        """Return every recorded backup, newest first."""
        with self._lock:
            records = self._read_manifest()
            indexed = list(enumerate(records))
            indexed.sort(
                key=lambda pair: (pair[1].created_at, pair[0]), reverse=True
            )
            return [record for _index, record in indexed]

    def verify(self, backup_id: str) -> bool:
        """Return ``True`` when the stored copy still matches its hash.

        A mismatch or an unreadable copy returns ``False``; an unknown id
        raises :class:`BackupError`.
        """
        record = self._record_for(backup_id)
        try:
            data, digest = self._guard.read_bytes(record.backup_relative)
        except WorkspaceError:
            # Missing or unreadable copy: the probe reports "not verified".
            return False
        return digest == record.sha256 and len(data) == record.size

    def restore(self, backup_id: str) -> ResolvedPath:
        """Restore a verified backup over its original workspace path.

        Returns the :class:`ResolvedPath` of the restored file.
        """
        record = self._record_for(backup_id)

        with self._lock:
            try:
                data, digest = self._guard.read_bytes(record.backup_relative)
            except (OperationLimitError, PathSecurityError):
                raise
            except WorkspaceError as exc:
                raise BackupError(
                    "The stored backup content is missing or unreadable.",
                    backup_id=backup_id,
                ) from exc

            if digest != record.sha256 or len(data) != record.size:
                raise IntegrityError(
                    "The stored backup content does not match the recorded "
                    "SHA-256.",
                    backup_id=backup_id,
                    expected=record.sha256,
                    actual=digest,
                )

            resolved, written_digest = self._guard.atomic_write_bytes(
                record.original_relative, data
            )
            if written_digest != record.sha256:
                raise IntegrityError(
                    "The restored content hash does not match the backup.",
                    backup_id=backup_id,
                    destination=resolved.relative,
                )

            self._audit_event(
                "success",
                {
                    "operation": "restore",
                    "backup_id": record.backup_id,
                    "original_relative": record.original_relative,
                    "sha256": record.sha256,
                },
            )
            return resolved

    # -- retention --------------------------------------------------------- #
    def prune(self) -> int:
        """Delete the oldest backups beyond retention; return the count.

        The newest backup is never deleted, even when ``retention`` is ``0``.
        """
        with self._lock:
            records = self._read_manifest()
            keep = max(1, self._retention)
            if len(records) <= keep:
                return 0

            removed = records[:-keep]
            kept = records[-keep:]
            self._write_manifest(kept)

            removed_ids: list[str] = []
            for record in removed:
                self._delete_backup(record)
                removed_ids.append(record.backup_id)

            self._audit_event(
                "success",
                {
                    "operation": "prune",
                    "removed": removed_ids,
                    "kept": len(kept),
                    "retention": self._retention,
                },
            )
            return len(removed)

    # -- manifest ---------------------------------------------------------- #
    def _read_manifest(self) -> list[BackupRecord]:
        resolved = self._guard.resolve(self._manifest_relative)
        if not resolved.exists:
            return []
        try:
            data, _digest = self._guard.read_bytes(self._manifest_relative)
        except (OperationLimitError, PathSecurityError):
            raise
        except WorkspaceError as exc:
            raise BackupError(
                "The backup manifest cannot be read.",
                path=self._manifest_relative,
            ) from exc

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BackupError(
                "The backup manifest is not valid UTF-8.",
                path=self._manifest_relative,
            ) from exc

        records: list[BackupRecord] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BackupError(
                    "The backup manifest contains invalid JSON.",
                    path=self._manifest_relative,
                    line=line_number,
                ) from exc
            records.append(_record_from_payload(payload, line_number))
        return records

    def _write_manifest(self, records: list[BackupRecord]) -> None:
        """Atomically replace the manifest with *records* in order."""
        if records:
            lines = [
                json.dumps(
                    _record_payload(record),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for record in records
            ]
            payload = ("\n".join(lines) + "\n").encode("utf-8")
        else:
            payload = b""
        try:
            self._guard.atomic_write_bytes(
                self._manifest_relative, payload, create_parents=True
            )
        except OperationLimitError:
            raise
        except WorkspaceError as exc:
            raise BackupError(
                "The backup manifest cannot be written.",
                path=self._manifest_relative,
            ) from exc

    def _append_manifest(self, record: BackupRecord) -> None:
        """Append one record durably without losing existing lines.

        :class:`PathGuard` intentionally exposes only atomic whole-file writes,
        so append semantics are implemented by preserving the existing bytes
        verbatim and writing ``existing + new line`` atomically.  The
        temporary file is ``fsync``-ed by ``atomic_write_bytes`` before the
        replacement, so the new record is durable.
        """
        existing = b""
        resolved = self._guard.resolve(self._manifest_relative)
        if resolved.exists:
            try:
                existing, _digest = self._guard.read_bytes(
                    self._manifest_relative
                )
            except (OperationLimitError, PathSecurityError):
                raise
            except WorkspaceError as exc:
                raise BackupError(
                    "The backup manifest cannot be read.",
                    path=self._manifest_relative,
                ) from exc
        if existing and not existing.endswith(b"\n"):
            existing += b"\n"

        line = json.dumps(
            _record_payload(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._write_manifest_bytes(existing + line + b"\n")

    def _write_manifest_bytes(self, payload: bytes) -> None:
        try:
            self._guard.atomic_write_bytes(
                self._manifest_relative, payload, create_parents=True
            )
        except OperationLimitError:
            raise
        except WorkspaceError as exc:
            raise BackupError(
                "The backup manifest cannot be written.",
                path=self._manifest_relative,
            ) from exc

    # -- records / deletion ------------------------------------------------ #
    def _record_for(self, backup_id: str) -> BackupRecord:
        if not isinstance(backup_id, str) or not backup_id.strip():
            raise ValueError("backup_id must be a non-empty string.")
        with self._lock:
            for record in self._read_manifest():
                if record.backup_id == backup_id:
                    return record
        raise BackupError("Unknown backup id.", backup_id=backup_id)

    def _backup_directory_relative(self, backup_id: str) -> str:
        return f"{self._directory}/{backup_id}"

    def _delete_backup(self, record: BackupRecord) -> None:
        """Delete the stored copy for *record*; missing copies are tolerated."""
        resolved = self._guard.resolve(
            self._backup_directory_relative(record.backup_id)
        )
        if not resolved.exists:
            return
        try:
            if resolved.is_dir:
                shutil.rmtree(resolved.absolute)
            else:  # pragma: no cover - tampered layout
                resolved.absolute.unlink()
        except OSError as exc:
            raise BackupError(
                "A pruned backup could not be deleted.",
                backup_id=record.backup_id,
                error=exc.strerror or "unknown",
            ) from exc

    def _discard(self, backup_id: str) -> None:
        """Best-effort removal of a backup directory that was not recorded."""
        try:
            resolved = self._guard.resolve(
                self._backup_directory_relative(backup_id)
            )
            if not resolved.exists:
                return
            if resolved.is_dir:
                shutil.rmtree(resolved.absolute, ignore_errors=True)
            else:  # pragma: no cover - tampered layout
                resolved.absolute.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - cleanup failure is not the error
            return

    # -- audit ------------------------------------------------------------- #
    def _audit_event(self, outcome: str, details: dict[str, Any]) -> None:
        if self._audit_log is None:
            return
        self._audit_log.record(
            action="workspace.backup",
            actor="backup_store",
            outcome=outcome,
            details=details,
        )


__all__ = [
    "BackupRecord",
    "BackupStore",
    "DEFAULT_DIRECTORY",
    "MANIFEST_NAME",
]
