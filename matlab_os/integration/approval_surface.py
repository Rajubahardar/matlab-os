"""Human approval surface for MATLAB mutations.

The agent side and the human side are separate by construction:

* The agent submits a :class:`MutationDescriptor` (inspectable, human readable)
  to a queue directory.  It never signs anything.
* A human runs ``python -m matlab_os.integration.approval_cli`` in their own
  shell.  That process holds the signing key and prints the exact operation for
  review before requiring a typed confirmation.
* Redemption verifies an HMAC ticket bound to tool, canonical arguments,
  normalised target, actor, request id and expiry, and consumes it once.

Trust boundary, stated plainly: on a single Windows user account the key file is
readable by any process of that user, so this is a *policy and ergonomics*
boundary, not an OS sandbox.  Do not describe it as protection against a
malicious agent running as the same user; use a separate account/ACL if that
threat is in scope.

Mutations stay disabled unless the caller also enables them in
``MatlabToolPolicy``; an approval alone never mutates anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from matlab_os.integration.matlab_approval import approval_target
from matlab_os.integration.matlab_policy import MatlabToolPolicy, OperationClass

DEFAULT_TTL_SECONDS = 600.0
DEFAULT_QUEUE = Path.home() / ".matlab-os" / "approvals"
DEFAULT_KEY_FILE = Path.home() / ".matlab-os" / "approval.key"

CONFIRMATION_TEMPLATE = "APPROVE {token_id}"

AuditHook = Callable[[dict[str, Any]], None]


class ApprovalSurfaceError(PermissionError):
    """Raised for any approval that cannot be honoured."""


def _canonical(arguments: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(arguments), sort_keys=True, separators=(",", ":"), default=str
    )


def _normalise(path: str) -> str:
    text = str(path or "").strip()
    return os.path.normcase(os.path.abspath(text)) if text else ""


@dataclass(frozen=True)
class MutationDescriptor:
    """Exactly what a human is being asked to authorise."""

    tool: str
    arguments: Mapping[str, Any]
    target: str
    actor: str
    request_id: str

    def digest(self) -> str:
        material = "\n".join(
            [
                self.tool,
                _canonical(self.arguments),
                _normalise(self.target),
                self.actor,
                self.request_id,
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "target": self.target,
            "actor": self.actor,
            "request_id": self.request_id,
            "digest": self.digest(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MutationDescriptor":
        return cls(
            tool=str(data["tool"]),
            arguments=dict(data.get("arguments") or {}),
            target=str(data.get("target") or ""),
            actor=str(data.get("actor") or ""),
            request_id=str(data.get("request_id") or ""),
        )


@dataclass
class ApprovalRecord:
    token_id: str
    descriptor: MutationDescriptor
    operation: str
    created_at: float
    expires_at: float
    status: str = "pending"  # pending | approved | rejected | consumed
    approver: str | None = None
    decided_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "operation": self.operation,
            "status": self.status,
            "approver": self.approver,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "decided_at": self.decided_at,
            "descriptor": self.descriptor.to_dict(),
        }


class ApprovalSurface:
    """Queue + signed ticket lifecycle for human approvals."""

    def __init__(
        self,
        *,
        queue_dir: str | Path | None = None,
        key_file: str | Path | None = None,
        policy: MatlabToolPolicy | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        audit: AuditHook | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.queue_dir = Path(queue_dir or DEFAULT_QUEUE)
        self.queue_file = self.queue_dir / "requests.jsonl"
        self.key_file = Path(key_file or DEFAULT_KEY_FILE)

        self.policy = policy or MatlabToolPolicy()
        self.ttl_seconds = float(ttl_seconds)
        self._audit = audit
        self._clock = clock

        self._records: dict[str, ApprovalRecord] = {}
        self._consumed: set[str] = set()

        self._load()

    # -- persistence ------------------------------------------------------- #
    def _load(self) -> None:
        if not self.queue_file.is_file():
            return
        for line in self.queue_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            descriptor = MutationDescriptor.from_dict(data["descriptor"])
            record = ApprovalRecord(
                token_id=data["token_id"],
                descriptor=descriptor,
                operation=data["operation"],
                created_at=data["created_at"],
                expires_at=data["expires_at"],
                status=data.get("status", "pending"),
                approver=data.get("approver"),
                decided_at=data.get("decided_at"),
            )
            self._records[record.token_id] = record
            if record.status == "consumed":
                self._consumed.add(record.token_id)

    def _append(self, record: ApprovalRecord) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        with self.queue_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    # -- key handling ------------------------------------------------------ #
    def _read_key(self) -> bytes:
        if not self.key_file.is_file():
            raise ApprovalSurfaceError(
                f"Approval signing key not found at {self.key_file}. "
                "A human must run: python -m matlab_os.integration.approval_cli init"
            )
        key = self.key_file.read_bytes().strip()
        if len(key) < 32:
            raise ApprovalSurfaceError("Approval key is too short; re-run init")
        return key

    def _sign(self, material: str) -> str:
        return hmac.new(self._read_key(), material.encode("utf-8"), hashlib.sha256).hexdigest()

    def _emit(self, event: str, **details: Any) -> None:
        if self._audit:
            self._audit({"action": f"matlab.approval_surface.{event}", **details})

    # -- agent side -------------------------------------------------------- #
    def submit(
        self,
        *,
        tool: str,
        arguments: Mapping[str, Any],
        actor: str,
        request_id: str,
        target: str | None = None,
    ) -> dict[str, Any]:
        """Create a pending approval request.  The agent may call this."""

        operation = self.policy.classify(tool, arguments)
        if operation is OperationClass.UNKNOWN:
            raise ApprovalSurfaceError(f"Unclassified tool cannot be approved: {tool}")
        if operation is OperationClass.READ and not self.policy.decide(
            tool, arguments
        ).get("requires_approval"):
            raise ApprovalSurfaceError(f"{tool} is read-only; no approval is required")

        descriptor = MutationDescriptor(
            tool=tool,
            arguments=dict(arguments),
            target=target or approval_target(arguments, self.queue_dir),
            actor=actor,
            request_id=request_id,
        )
        now = self._clock()
        record = ApprovalRecord(
            token_id=secrets.token_hex(8),
            descriptor=descriptor,
            operation=operation.value,
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._records[record.token_id] = record
        self._append(record)
        self._emit("submitted", token_id=record.token_id, tool=tool, request_id=request_id)
        return record.to_dict()

    def pending(self) -> list[dict[str, Any]]:
        return [
            record.to_dict()
            for record in self._records.values()
            if record.status == "pending" and self._clock() <= record.expires_at
        ]

    # -- human side -------------------------------------------------------- #
    def decide(
        self,
        token_id: str,
        *,
        approve: bool,
        approver: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Approve or reject.  Requires the key file and an exact confirmation."""

        record = self._records.get(token_id)
        if record is None:
            raise ApprovalSurfaceError("Unknown approval request")
        if record.status != "pending":
            raise ApprovalSurfaceError(f"Request is already {record.status}")
        if self._clock() > record.expires_at:
            record.status = "rejected"
            self._append(record)
            raise ApprovalSurfaceError("Approval request expired")
        if not str(approver or "").strip():
            raise ApprovalSurfaceError("An approver identity is required")

        if approve and str(approver).strip().casefold() == str(record.descriptor.actor).strip().casefold():
            raise ApprovalSurfaceError(
                f"Self-approval prohibited: approver '{approver}' cannot match task requester '{record.descriptor.actor}'"
            )

        expected = CONFIRMATION_TEMPLATE.format(token_id=token_id)
        if approve and confirmation != expected:
            raise ApprovalSurfaceError(
                f"Confirmation does not match. Type exactly: {expected}"
            )

        record.approver = str(approver).strip()
        record.decided_at = self._clock()
        record.status = "approved" if approve else "rejected"
        self._append(record)
        self._emit(
            "approved" if approve else "rejected",
            token_id=token_id,
            approver=record.approver,
        )

        if not approve:
            return {"status": "rejected", "token_id": token_id}

        material = f"{token_id}|{record.descriptor.digest()}|{record.approver}"
        return {
            "status": "approved",
            "token_id": token_id,
            "ticket": f"{token_id}.{self._sign(material)}",
            "expires_at": record.expires_at,
        }

    # -- redemption -------------------------------------------------------- #
    def redeem(
        self,
        descriptor: MutationDescriptor,
        ticket: str | None,
    ) -> ApprovalRecord:
        """Verify and consume a ticket for an exact operation."""

        if not ticket:
            self._emit("refused", reason="missing_ticket", tool=descriptor.tool)
            raise ApprovalSurfaceError("Mutation requires human approval")

        token_id, _, signature = str(ticket).partition(".")
        record = self._records.get(token_id)
        if record is None:
            self._emit("refused", reason="unknown_ticket")
            raise ApprovalSurfaceError("Unknown approval ticket")
        # Replay is checked before status so a consumed ticket reports the
        # attempt as a replay rather than as a generic wrong-state refusal.
        if token_id in self._consumed or record.status == "consumed":
            self._emit("refused", reason="replay", token_id=token_id)
            raise ApprovalSurfaceError("Approval ticket already used")
        if record.status != "approved":
            raise ApprovalSurfaceError(f"Approval is {record.status}")
        if self._clock() > record.expires_at:
            self._emit("refused", reason="expired", token_id=token_id)
            raise ApprovalSurfaceError("Approval ticket expired")

        material = f"{token_id}|{record.descriptor.digest()}|{record.approver}"
        if not hmac.compare_digest(self._sign(material), signature):
            self._emit("refused", reason="bad_signature", token_id=token_id)
            raise ApprovalSurfaceError("Approval ticket signature is invalid")

        checks = (
            ("tool_mismatch", record.descriptor.tool != descriptor.tool),
            ("actor_mismatch", record.descriptor.actor != descriptor.actor),
            ("request_mismatch", record.descriptor.request_id != descriptor.request_id),
            (
                "arguments_modified",
                _canonical(record.descriptor.arguments) != _canonical(descriptor.arguments),
            ),
            (
                "target_mismatch",
                _normalise(record.descriptor.target) != _normalise(descriptor.target),
            ),
        )
        for reason, failed in checks:
            if failed:
                self._emit("refused", reason=reason, token_id=token_id)
                raise ApprovalSurfaceError(f"Approval does not match this operation: {reason}")

        # Persist consumption so a restart cannot resurrect a used ticket.
        record.status = "consumed"
        self._consumed.add(token_id)
        self._append(record)
        self._emit("consumed", token_id=token_id, tool=descriptor.tool)
        return record


def create_key_file(path: str | Path | None = None, *, force: bool = False) -> Path:
    """Create the approval signing key.  Human-only action."""

    key_path = Path(path or DEFAULT_KEY_FILE)
    if key_path.exists() and not force:
        raise ApprovalSurfaceError(f"Key already exists at {key_path}")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(secrets.token_bytes(48) + b"\n")
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return key_path


__all__ = [
    "ApprovalSurface",
    "ApprovalSurfaceError",
    "MutationDescriptor",
    "create_key_file",
    "DEFAULT_QUEUE",
    "DEFAULT_KEY_FILE",
]
