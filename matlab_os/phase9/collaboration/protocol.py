"""Typed collaboration protocol for Phase 9 (staging only).

An :class:`AgentMessage` is the only structure exchanged between agents.  It is
strictly validated before use:

* sender, recipient, task, correlation and message identifiers are non-empty,
* the delegation depth is a non-negative integer,
* the payload is a dictionary whose canonical JSON form fits the configured
  byte budget,
* the schema version is exactly ``1``.

The payload is **redacted** whenever the message is serialised or digested, so
a protocol trace can never leak a credential.  The module performs no I/O.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..errors import CollaborationError
from ..security.redaction import redact

#: The only supported wire schema version.
SCHEMA_VERSION = 1


class MessageKind(str, Enum):
    """Purpose of a collaboration message."""

    DELEGATE = "delegate"
    ACCEPT = "accept"
    RESULT = "result"
    ERROR = "error"
    STATUS = "status"
    CANCEL = "cancel"
    CONFLICT = "conflict"

    def __str__(self) -> str:
        return self.value


def _canonical_json(payload: Any) -> str:
    """Serialise *payload* deterministically (sorted keys, no whitespace)."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


@dataclass
class AgentMessage:
    """One validated agent-to-agent message.

    ``created_at`` is supplied by the caller (normally the injected clock) so
    tests and audits are deterministic.  ``schema_version`` is fixed to
    :data:`SCHEMA_VERSION`.
    """

    message_id: str
    kind: MessageKind
    sender: str
    recipient: str
    task_id: str
    correlation_id: str
    depth: int
    payload: dict[str, Any]
    created_at: float
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.kind, MessageKind):
            return
        if isinstance(self.kind, str):
            try:
                self.kind = MessageKind(self.kind.strip().lower())
            except ValueError as exc:
                raise ValueError(
                    f"Unknown message kind: {self.kind!r}"
                ) from exc
            return
        raise ValueError("kind must be a MessageKind or its string value.")

    # -- validation --------------------------------------------------------- #
    def validate(self, max_payload_bytes: int) -> None:
        """Validate structure and payload budget.

        Raises:
            CollaborationError: when any invariant is violated, including a
                serialised payload larger than *max_payload_bytes*.
            ValueError: when *max_payload_bytes* is not a positive integer.
        """
        if (
            isinstance(max_payload_bytes, bool)
            or not isinstance(max_payload_bytes, int)
            or max_payload_bytes < 1
        ):
            raise ValueError(
                "max_payload_bytes must be a positive integer."
            )
        self._validate_structure()

        size = len(
            _canonical_json(self.payload).encode("utf-8")
        )
        if size > max_payload_bytes:
            raise CollaborationError(
                "AgentMessage payload exceeds the configured limit.",
                payload_bytes=size,
                max_payload_bytes=max_payload_bytes,
                message_id=self.message_id,
            )

    def _validate_structure(self) -> None:
        """Validate the non-size invariants (used by deserialisation too)."""
        for field_name in (
            "message_id",
            "sender",
            "recipient",
            "task_id",
            "correlation_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise CollaborationError(
                    f"AgentMessage.{field_name} must be a non-empty string."
                )
        if not isinstance(self.kind, MessageKind):
            raise CollaborationError("AgentMessage.kind must be a MessageKind.")
        if (
            isinstance(self.depth, bool)
            or not isinstance(self.depth, int)
            or self.depth < 0
        ):
            raise CollaborationError(
                "AgentMessage.depth must be an integer >= 0."
            )
        if not isinstance(self.payload, dict):
            raise CollaborationError(
                "AgentMessage.payload must be a dictionary."
            )
        if isinstance(self.created_at, bool) or not isinstance(
            self.created_at, (int, float)
        ):
            raise CollaborationError(
                "AgentMessage.created_at must be a number."
            )
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != SCHEMA_VERSION
        ):
            raise CollaborationError(
                "Unsupported AgentMessage.schema_version: "
                f"{self.schema_version!r}"
            )

    # -- serialisation ------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe copy with the payload redacted."""
        return {
            "message_id": self.message_id,
            "kind": self.kind.value,
            "sender": self.sender,
            "recipient": self.recipient,
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "depth": self.depth,
            "payload": redact(self.payload),
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }

    def to_json(self) -> str:
        """Return the canonical, redacted JSON representation."""
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentMessage":
        """Rebuild a message from :meth:`to_dict` output.

        Raises:
            CollaborationError: on malformed input.  A redacted payload is
                accepted as-is (redaction is intentional and irreversible).
        """
        if not isinstance(payload, Mapping):
            raise CollaborationError(
                "AgentMessage payload must be a mapping when deserialised."
            )
        required = (
            "message_id",
            "kind",
            "sender",
            "recipient",
            "task_id",
            "correlation_id",
            "depth",
            "payload",
            "created_at",
        )
        missing = sorted(name for name in required if name not in payload)
        if missing:
            raise CollaborationError(
                "AgentMessage is missing required field(s): "
                + ", ".join(missing)
            )
        raw_kind = payload["kind"]
        try:
            kind = (
                raw_kind
                if isinstance(raw_kind, MessageKind)
                else MessageKind(str(raw_kind).strip().lower())
            )
        except ValueError as exc:
            raise CollaborationError(
                f"Unknown AgentMessage kind: {raw_kind!r}"
            ) from exc
        body = payload["payload"]
        if not isinstance(body, Mapping):
            raise CollaborationError(
                "AgentMessage.payload must be a mapping when deserialised."
            )
        message = cls(
            message_id=payload["message_id"],
            kind=kind,
            sender=payload["sender"],
            recipient=payload["recipient"],
            task_id=payload["task_id"],
            correlation_id=payload["correlation_id"],
            depth=payload["depth"],
            payload=dict(body),
            created_at=payload["created_at"],
            schema_version=payload.get("schema_version", SCHEMA_VERSION),
        )
        message._validate_structure()
        return message

    @classmethod
    def from_json(cls, text: str) -> "AgentMessage":
        """Parse a message from its canonical JSON representation."""
        if not isinstance(text, str):
            raise ValueError("text must be a string.")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CollaborationError(
                "AgentMessage JSON is not valid JSON."
            ) from exc
        if not isinstance(payload, Mapping):
            raise CollaborationError(
                "AgentMessage JSON root must be an object."
            )
        return cls.from_dict(payload)

    def digest(self) -> str:
        """Return the SHA-256 of the canonical (redacted) message JSON."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


__all__ = [
    "SCHEMA_VERSION",
    "MessageKind",
    "AgentMessage",
]
