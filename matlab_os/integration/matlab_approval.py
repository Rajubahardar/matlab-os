"""Per-operation human approval for MATLAB / Simulink mutations.

The approval is bound to one operation and cannot be reused or reshaped:

* **operation / tool specific** - the ticket records the tool name;
* **argument bound** - the canonical JSON of the arguments is digested;
* **target bound** - the model or file path is normalised and digested;
* **request bound** - the caller's request id must match;
* **actor bound** - only the actor that requested it can redeem it;
* **time limited** - the ticket expires (default 300 s);
* **single use** - redemption consumes the ticket, so replays fail;
* **audited** - every issue, grant, refusal and redemption is reported.

Tickets are HMAC-signed with a per-authority secret, so a forged or edited
ticket fails verification before any state is consulted.

Mutations remain disabled unless the caller both passes the policy gates and
presents a valid, unexpired, unconsumed ticket for the exact operation.
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

from matlab_os.integration.matlab_policy import (
    MatlabToolPolicy,
    OperationClass,
)

AuditHook = Callable[[dict[str, Any]], None]

DEFAULT_TTL_SECONDS = 300.0


class ApprovalError(PermissionError):
    """Raised when a mutation is attempted without a valid approval."""


def _canonical(arguments: Mapping[str, Any]) -> str:
    """Stable JSON so argument order cannot change the digest."""

    return json.dumps(
        dict(arguments), sort_keys=True, separators=(",", ":"), default=str
    )


def _normalise_target(target: str) -> str:
    text = str(target or "").strip()
    if not text:
        return ""
    return os.path.normcase(os.path.abspath(text))


@dataclass(frozen=True)
class MutationRequest:
    """One concrete mutation the operator is asked to approve."""

    tool: str
    arguments: Mapping[str, Any]
    target: str
    actor: str
    request_id: str

    def canonical_arguments(self) -> str:
        return _canonical(self.arguments)

    def normalised_target(self) -> str:
        return _normalise_target(self.target)

    def digest(self) -> str:
        material = "\n".join(
            [
                self.tool,
                self.canonical_arguments(),
                self.normalised_target(),
                self.actor,
                self.request_id,
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class PendingApproval:
    """Server-side record of an issued approval request."""

    token_id: str
    request: MutationRequest
    operation: OperationClass
    issued_at: float
    expires_at: float
    approver: str | None = None
    granted_at: float | None = None
    consumed_at: float | None = None

    @property
    def granted(self) -> bool:
        return self.approver is not None

    @property
    def consumed(self) -> bool:
        return self.consumed_at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "tool": self.request.tool,
            "operation": self.operation.value,
            "target": self.request.normalised_target(),
            "actor": self.request.actor,
            "request_id": self.request.request_id,
            "digest": self.request.digest(),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "approver": self.approver,
            "consumed": self.consumed,
        }


class ApprovalAuthority:
    """Issues, grants, verifies and consumes mutation approvals."""

    def __init__(
        self,
        *,
        policy: MatlabToolPolicy | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        secret: bytes | None = None,
        audit: AuditHook | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

        self.policy = policy if policy is not None else MatlabToolPolicy()
        self.ttl_seconds = float(ttl_seconds)
        self._secret = secret or secrets.token_bytes(32)
        self._audit = audit
        self._clock = clock
        self._pending: dict[str, PendingApproval] = {}

    # -- internals --------------------------------------------------------- #
    def _emit(self, event: str, **details: Any) -> None:
        if self._audit is None:
            return
        self._audit({"action": f"matlab.approval.{event}", **details})

    def _sign(self, token_id: str, digest: str, approver: str) -> str:
        material = f"{token_id}|{digest}|{approver}".encode("utf-8")
        return hmac.new(self._secret, material, hashlib.sha256).hexdigest()

    # -- lifecycle --------------------------------------------------------- #
    def request_approval(self, request: MutationRequest) -> PendingApproval:
        """Register a mutation awaiting human approval."""

        operation = self.policy.classify(request.tool, request.arguments)
        if operation is OperationClass.UNKNOWN:
            self._emit("refused", tool=request.tool, reason="unclassified_operation")
            raise ApprovalError(
                f"Cannot request approval for unclassified tool: {request.tool}"
            )
        if operation is OperationClass.READ:
            raise ApprovalError(
                f"{request.tool} is read-only; no approval is required"
            )

        now = self._clock()
        pending = PendingApproval(
            token_id=secrets.token_hex(16),
            request=request,
            operation=operation,
            issued_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._pending[pending.token_id] = pending
        self._emit(
            "requested",
            token_id=pending.token_id,
            tool=request.tool,
            operation=operation.value,
            target=pending.request.normalised_target(),
            actor=request.actor,
            request_id=request.request_id,
            expires_at=pending.expires_at,
        )
        return pending

    def approve(self, token_id: str, approver: str) -> str:
        """Grant a pending request and return the single-use ticket."""

        pending = self._pending.get(token_id)
        if pending is None:
            self._emit("refused", token_id=token_id, reason="unknown_approval")
            raise ApprovalError("Unknown approval request")
        if not str(approver or "").strip():
            raise ApprovalError("An approver identity is required")
        if pending.consumed:
            self._emit("refused", token_id=token_id, reason="already_consumed")
            raise ApprovalError("Approval already consumed")
        if self._clock() > pending.expires_at:
            self._emit("refused", token_id=token_id, reason="expired")
            raise ApprovalError("Approval request expired")

        pending.approver = str(approver).strip()
        pending.granted_at = self._clock()
        ticket = f"{token_id}.{self._sign(token_id, pending.request.digest(), pending.approver)}"
        self._emit(
            "granted",
            token_id=token_id,
            approver=pending.approver,
            tool=pending.request.tool,
        )
        return ticket

    def authorize(
        self,
        request: MutationRequest,
        ticket: str | None,
    ) -> PendingApproval:
        """Verify a ticket for ``request``, then consume it."""

        if not ticket:
            self._emit("refused", tool=request.tool, reason="missing_ticket")
            raise ApprovalError(
                f"{request.tool} requires explicit human approval"
            )

        token_id, _, signature = str(ticket).partition(".")
        pending = self._pending.get(token_id)
        if pending is None:
            self._emit("refused", tool=request.tool, reason="unknown_approval")
            raise ApprovalError("Unknown approval ticket")

        if not pending.granted:
            self._emit("refused", token_id=token_id, reason="not_granted")
            raise ApprovalError("Approval was never granted")

        expected = self._sign(token_id, pending.request.digest(), pending.approver or "")
        if not hmac.compare_digest(expected, signature):
            self._emit("refused", token_id=token_id, reason="bad_signature")
            raise ApprovalError("Approval ticket signature is invalid")

        if pending.consumed:
            self._emit("refused", token_id=token_id, reason="replay")
            raise ApprovalError("Approval ticket has already been used")

        if self._clock() > pending.expires_at:
            self._emit("refused", token_id=token_id, reason="expired")
            raise ApprovalError("Approval ticket expired")

        checks: tuple[tuple[str, bool], ...] = (
            ("tool_mismatch", pending.request.tool != request.tool),
            ("actor_mismatch", pending.request.actor != request.actor),
            ("request_mismatch", pending.request.request_id != request.request_id),
            (
                "arguments_modified",
                pending.request.canonical_arguments()
                != request.canonical_arguments(),
            ),
            (
                "target_mismatch",
                pending.request.normalised_target() != request.normalised_target(),
            ),
        )
        for reason, failed in checks:
            if failed:
                self._emit("refused", token_id=token_id, reason=reason)
                raise ApprovalError(f"Approval does not match this operation: {reason}")

        pending.consumed_at = self._clock()
        self._emit(
            "consumed",
            token_id=token_id,
            tool=request.tool,
            approver=pending.approver,
            request_id=request.request_id,
        )
        return pending

    # -- inspection -------------------------------------------------------- #
    def pending(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self._pending.values() if p.consumed_at is None]

    def history(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self._pending.values()]


def approval_target(arguments: Mapping[str, Any], fallback: str) -> str:
    """Best-effort target identity for a mutation.

    Model and file arguments win; otherwise the workspace root is the target, so
    a ticket cannot be moved to a different workspace.
    """

    for key in ("model", "file", "path", "filename", "target"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return str(Path(fallback))


__all__ = [
    "ApprovalAuthority",
    "ApprovalError",
    "MutationRequest",
    "PendingApproval",
    "approval_target",
    "DEFAULT_TTL_SECONDS",
]
