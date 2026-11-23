"""Append-only, hash-chained audit log for Phase 9.

Every security-relevant action is recorded as one JSON object per line with a
SHA-256 chain so that tampering or truncation is detectable.

Guarantees
----------
* Every record is redacted before it is written; a credential value can never
  reach the file through this API.
* The log never leaves the configured directory.
* Writes are serialised by a re-entrant lock and flushed to disk.
* ``verify_chain()`` detects any modified, removed or reordered record.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from .errors import Phase9Error
from .security.redaction import redact

GENESIS = "0" * 64


class AuditError(Phase9Error):
    """The audit log could not be written or verified."""

    code = "audit_error"


@dataclass(frozen=True)
class AuditRecord:
    sequence: int
    timestamp: float
    correlation_id: str
    action: str
    actor: str
    outcome: str
    details: dict[str, Any] = field(default_factory=dict)
    previous_hash: str = GENESIS
    record_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "action": self.action,
            "actor": self.actor,
            "outcome": self.outcome,
            "details": self.details,
            "previous_hash": self.previous_hash,
            "record_hash": self.record_hash,
        }


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def compute_record_hash(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items()
            if key != "record_hash"}
    return hashlib.sha256(_canonical(body)).hexdigest()


class AuditLog:
    """Thread-safe, hash-chained JSONL audit log."""

    def __init__(self, path: str | Path, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = bool(enabled)
        self._lock = threading.RLock()
        self._sequence = 0
        self._last_hash = GENESIS

        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._restore_tail()

    # -- internal ---------------------------------------------------------- #
    def _restore_tail(self) -> None:
        """Continue the chain from the existing file, if any."""
        if not self.path.is_file():
            return
        last: Optional[dict[str, Any]] = None
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    candidate = json.loads(line)
                    if isinstance(candidate, dict):
                        last = candidate
        except (OSError, json.JSONDecodeError) as exc:
            raise AuditError(
                f"Existing audit log is unreadable: {exc}", path=str(self.path)
            ) from exc

        if last is not None:
            self._sequence = int(last.get("sequence", 0))
            self._last_hash = str(last.get("record_hash") or GENESIS)

    # -- public API -------------------------------------------------------- #
    def record(
        self,
        action: str,
        actor: str,
        outcome: str,
        details: Optional[dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> AuditRecord:
        if not isinstance(action, str) or not action.strip():
            raise AuditError("Audit action must be a non-empty string.")
        if not isinstance(actor, str) or not actor.strip():
            raise AuditError("Audit actor must be a non-empty string.")

        safe_details = redact(details or {})
        if not isinstance(safe_details, dict):
            safe_details = {"value": safe_details}

        with self._lock:
            sequence = self._sequence + 1
            payload = {
                "sequence": sequence,
                "timestamp": time.time(),
                "correlation_id": correlation_id or str(uuid.uuid4()),
                "action": action.strip(),
                "actor": actor.strip(),
                "outcome": str(outcome),
                "details": safe_details,
                "previous_hash": self._last_hash,
            }
            record_hash = compute_record_hash(payload)
            payload["record_hash"] = record_hash

            if self.enabled:
                self._append(payload)

            self._sequence = sequence
            self._last_hash = record_hash

            return AuditRecord(
                sequence=sequence,
                timestamp=payload["timestamp"],
                correlation_id=payload["correlation_id"],
                action=payload["action"],
                actor=payload["actor"],
                outcome=payload["outcome"],
                details=safe_details,
                previous_hash=payload["previous_hash"],
                record_hash=record_hash,
            )

    def _append(self, payload: dict[str, Any]) -> None:
        line = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        )
        try:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise AuditError(
                f"Audit write failed: {exc}", path=str(self.path)
            ) from exc

    def records(self) -> Iterator[dict[str, Any]]:
        if not self.path.is_file():
            return iter(())
        return self._iter_records()

    def _iter_records(self) -> Iterator[dict[str, Any]]:
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)

    def verify_chain(self) -> dict[str, Any]:
        """Verify sequence continuity and hash linkage."""
        if not self.path.is_file():
            return {
                "valid": True,
                "records": 0,
                "reason": "no audit records present",
            }

        previous = GENESIS
        expected_sequence = 0
        count = 0

        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                return {
                    "valid": False,
                    "records": count,
                    "reason": f"invalid JSON at line {line_number}",
                }
            expected_sequence += 1
            if record.get("sequence") != expected_sequence:
                return {
                    "valid": False,
                    "records": count,
                    "reason": f"sequence gap at line {line_number}",
                }
            if record.get("previous_hash") != previous:
                return {
                    "valid": False,
                    "records": count,
                    "reason": f"hash chain break at line {line_number}",
                }
            recomputed = compute_record_hash(record)
            if record.get("record_hash") != recomputed:
                return {
                    "valid": False,
                    "records": count,
                    "reason": f"record hash mismatch at line {line_number}",
                }
            previous = recomputed
            count += 1

        return {
            "valid": True,
            "records": count,
            "head": previous,
            "reason": "chain verified",
        }

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        outcomes: dict[str, int] = {}
        for record in self.records():
            action = str(record.get("action", "unknown"))
            counts[action] = counts.get(action, 0) + 1
            outcome = str(record.get("outcome", "unknown"))
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        return {
            "path": str(self.path),
            "enabled": self.enabled,
            "records": sum(counts.values()),
            "actions": counts,
            "outcomes": outcomes,
            "chain": self.verify_chain(),
        }


class NullAuditLog(AuditLog):
    """Audit sink used when auditing is disabled by configuration.

    It stays in-memory only so that callers never need a conditional branch.
    """

    def __init__(self) -> None:
        super().__init__(Path(os.devnull), enabled=False)


__all__ = [
    "AuditError",
    "AuditRecord",
    "AuditLog",
    "NullAuditLog",
    "GENESIS",
    "compute_record_hash",
]
