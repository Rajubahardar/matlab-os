"""Unified agent ecosystem contracts.

Common typed contracts for the Phase 12 agent ecosystem. All other
components consume these shapes; they never invent their own private variants.

Contracts deliberately keep only what is needed for a safe lifecycle:
identity, capability declarations, capability-based selection, resource
requirements, task execution, coordination and validation.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping

from matlab_os.phase9.agents.capability import AgentCapability, AgentProfile

# ---------------------------------------------------------------------- #
# Task contract                                                         #
# ---------------------------------------------------------------------- #

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _non_empty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value.strip()


def _non_empty_map(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping.")
    return value


def _inspectable_mapping(value: Any, label: str) -> Mapping[str, Any]:
    mapping = _non_empty_map(value, label)
    for key, item in mapping.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{label} keys must be non-empty strings.")
        if isinstance(item, bool):
            raise ValueError(f"{label} values must be plain data.")
        if isinstance(item, (str, int, float)) or item is None:
            continue
        if isinstance(item, Mapping) or isinstance(item, list):
            continue
        raise ValueError(f"{label} values must be plain data.")
    return mapping


@dataclass(frozen=False)
class TaskContract:
    """Single immutable task unit used by every agent in the ecosystem."""

    task_id: str
    objective: str
    inputs: Mapping[str, Any]
    context_references: Mapping[str, Any] = field(default_factory=dict)
    constraints: Mapping[str, Any] = field(default_factory=dict)
    required_capabilities: tuple[str, ...] = ()
    timeout: float = 300.0
    deadline: datetime | None = None
    actor: str = "operator"
    workload_signature: str = ""

    def __post_init__(self) -> None:
        _non_empty_text(self.task_id, "task_id")
        if not _TASK_ID_RE.fullmatch(self.task_id):
            raise ValueError("task_id must be a short stable identifier.")
        _non_empty_text(self.objective, "objective")
        object.__setattr__(self, "inputs", _inspectable_mapping(self.inputs, "inputs"))
        object.__setattr__(self, "context_references", _inspectable_mapping(
            self.context_references, "context_references"
        ))
        object.__setattr__(self, "constraints", _inspectable_mapping(self.constraints, "constraints"))
        object.__setattr__(self, "required_capabilities", tuple(sorted(self.required_capabilities)))
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)):
            raise ValueError("timeout must be a positive number of seconds.")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive.")
        if self.deadline is not None:
            if not isinstance(self.deadline, datetime):
                raise ValueError("deadline must be a datetime.")
            if self.deadline.tzinfo is None:
                raise ValueError("deadline must be timezone-aware.")
        if not isinstance(self.actor, str) or not self.actor.strip():
            raise ValueError("actor must be a non-empty string.")

    @property
    def signature(self) -> str:
        # Stable replay/duplicate signature.  The actor and deadline are part
        # of the identity; inputs are what can be hashed without exposing
        # secrets because the signature is only used for duplicate detection.
        payload = {
            "task_id": self.task_id,
            "objective": self.objective,
            "actor": self.actor,
            "inputs": self.inputs,
            "context_references": self.context_references,
            "constraints": self.constraints,
            "required_capabilities": self.required_capabilities,
            "timeout": self.timeout,
            "deadline": self.deadline.isoformat() if self.deadline else None,
        }
        return hashlib.sha256(
            str(payload).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "inputs": dict(self.inputs),
            "context_references": dict(self.context_references),
            "constraints": dict(self.constraints),
            "required_capabilities": list(self.required_capabilities),
            "timeout": self.timeout,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "actor": self.actor,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TaskContract":
        if not isinstance(payload, Mapping):
            raise ValueError("task payload must be a mapping.")
        return cls(
            task_id=str(payload["task_id"]),
            objective=str(payload["objective"]),
            inputs=dict(payload.get("inputs", {})),
            context_references=dict(payload.get("context_references", {})),
            constraints=dict(payload.get("constraints", {})),
            required_capabilities=tuple(payload.get("required_capabilities", ())),
            timeout=float(payload.get("timeout", 300.0)),
            deadline=datetime.fromisoformat(str(payload["deadline"]))
            if payload.get("deadline") else None,
            actor=str(payload.get("actor", "operator")),
        )


# ---------------------------------------------------------------------- #
# Result contract                                                       #
# ---------------------------------------------------------------------- #

@dataclass(frozen=False)
class ResultContract:
    """Structured execution result, never a raw throwable object."""

    task_id: str
    agent_id: str
    status: str
    output: Mapping[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    duration_seconds: float = 0.0
    resource_usage: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    verification_status: str = "unverified"
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _non_empty_text(self.task_id, "task_id")
        _non_empty_text(self.agent_id, "agent_id")
        _non_empty_text(self.status, "status")
        object.__setattr__(self, "output", _inspectable_mapping(self.output, "output"))
        object.__setattr__(self, "errors", tuple(str(e) for e in self.errors))
        object.__setattr__(self, "warnings", tuple(str(w) for w in self.warnings))
        if not isinstance(self.duration_seconds, (int, float)) or self.duration_seconds < 0:
            raise ValueError("duration_seconds must be a non-negative number.")
        self.resource_usage = _inspectable_mapping(self.resource_usage, "resource_usage")
        self.evidence_refs = tuple(str(e) for e in self.evidence_refs)
        _non_empty_text(self.verification_status, "verification_status")
        self.provenance = tuple(str(p) for p in self.provenance)

    @property
    def is_failure(self) -> bool:
        return self.status.upper() in {"FAILED", "FAILURE", "ERROR", "TIMEOUT", "CANCELLED"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "output": dict(self.output),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "duration_seconds": self.duration_seconds,
            "resource_usage": dict(self.resource_usage),
            "evidence_refs": list(self.evidence_refs),
            "verification_status": self.verification_status,
            "provenance": list(self.provenance),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResultContract":
        if not isinstance(payload, Mapping):
            raise ValueError("result payload must be a mapping.")
        return cls(
            task_id=str(payload["task_id"]),
            agent_id=str(payload["agent_id"]),
            status=str(payload["status"]),
            output=dict(payload.get("output", {})),
            errors=tuple(str(e) for e in payload.get("errors", ()) or ()),
            warnings=tuple(str(w) for w in payload.get("warnings", ()) or ()),
            duration_seconds=float(payload.get("duration_seconds", 0.0)),
            resource_usage=dict(payload.get("resource_usage", {})),
            evidence_refs=tuple(str(e) for e in payload.get("evidence_refs", ()) or ()),
            verification_status=str(payload.get("verification_status", "unverified")),
            provenance=tuple(str(p) for p in payload.get("provenance", ()) or ()),
        )


# ---------------------------------------------------------------------- #
# Agent contract                                                        #
# ---------------------------------------------------------------------- #

@dataclass(frozen=False)
class AgentContract:
    """A typed view of an already registered agent profile."""

    agent_id: str
    display_name: str
    status: str
    enabled: bool
    capabilities: Mapping[str, Mapping[str, Any]]
    tools: tuple[str, ...]
    permission_scope: tuple[str, ...]
    max_complexity: int
    max_concurrent_tasks: int
    locality: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _non_empty_text(self.agent_id, "agent_id")
        _non_empty_text(self.display_name, "display_name")
        _non_empty_text(self.status, "status")
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be bool.")
        if not isinstance(self.capabilities, Mapping):
            raise ValueError("capabilities must be a mapping.")
        if not isinstance(self.tools, tuple):
            raise ValueError("tools must be a tuple.")
        if not isinstance(self.permission_scope, tuple):
            raise ValueError("permission_scope must be a tuple.")
        if isinstance(self.max_complexity, bool) or not isinstance(self.max_complexity, int):
            raise ValueError("max_complexity must be int.")
        if self.max_complexity < 1:
            raise ValueError("max_complexity must be >= 1.")
        if isinstance(self.max_concurrent_tasks, bool) or not isinstance(self.max_concurrent_tasks, int):
            raise ValueError("max_concurrent_tasks must be int.")
        if self.max_concurrent_tasks < 1:
            raise ValueError("max_concurrent_tasks must be >= 1.")
        if self.locality not in {"local", "remote"}:
            raise ValueError("locality must be local or remote.")
        object.__setattr__(self, "metadata", _inspectable_mapping(self.metadata, "metadata"))

    @property
    def capabilities_map(self) -> dict[str, AgentCapability]:
        # Late import avoids a hard dependency on the Phase 9 capability module
        # for callers that only validate the raw contract.  It is always
        # available in this tree, though.
        from matlab_os.phase9.agents.capability import AgentCapability

        normalized: dict[str, AgentCapability] = {}
        for name, value in self.capabilities.items():
            capability = value
            if isinstance(value, AgentCapability):
                capability = value
            elif isinstance(value, Mapping):
                capability = AgentCapability.from_dict(value)
            else:
                raise ValueError(f"Unknown capability entry for {name!r}.")
            normalized[name] = capability
        return normalized

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities_map

    def missing_capabilities(self, required: Mapping[str, Any] | tuple[str, ...] | list[str] | set[str]) -> list[str]:
        if isinstance(required, Mapping):
            required_set = set(required.keys())
        elif isinstance(required, (list, tuple, set)):
            required_set = set(required)
        else:
            required_set = set(self.capabilities)
        return sorted(name for name in required_set if not self.has_capability(name))

    def to_dict(self) -> dict[str, Any]:
        from matlab_os.phase9.security.redaction import redact

        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "status": self.status,
            "enabled": self.enabled,
            "capabilities": {
                name: capability.to_dict() if hasattr(capability, "to_dict") else dict(capability)
                for name, capability in self.capabilities_map.items()
            },
            "tools": list(self.tools),
            "permission_scope": list(self.permission_scope),
            "max_complexity": self.max_complexity,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "locality": self.locality,
            "metadata": redact(dict(self.metadata)),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentContract":
        if not isinstance(payload, Mapping):
            raise ValueError("agent payload must be a mapping.")
        return cls(
            agent_id=str(payload["agent_id"]),
            display_name=str(payload.get("display_name", "")),
            status=str(payload.get("status", "available")),
            enabled=bool(payload.get("enabled", True)),
            capabilities=payload.get("capabilities", {}),
            tools=tuple(payload.get("tools", ())),
            permission_scope=tuple(payload.get("permission_scope", ())),
            max_complexity=int(payload.get("max_complexity", 1)),
            max_concurrent_tasks=int(payload.get("max_concurrent_tasks", 1)),
            locality=str(payload.get("locality", "local")),
            metadata=payload.get("metadata", {}),
        )


# ---------------------------------------------------------------------- #
# Resource requirements                                                 #
# ---------------------------------------------------------------------- #

@dataclass(frozen=False)
class ResourceRequirements:
    task_id: str
    ram_gb: float = 0.0
    cpu_cores: float = 0.0
    gpu_memory_gb: float = 0.0
    model_name: str | None = None
    max_temperature_c: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.task_id, bool) or not isinstance(self.task_id, str):
            raise ValueError("task_id must be a string.")
        if self.task_id:
            ResourceRequirements._validate_non_negative("ram_gb", self.ram_gb)
            ResourceRequirements._validate_non_negative("cpu_cores", self.cpu_cores)
            ResourceRequirements._validate_non_negative("gpu_memory_gb", self.gpu_memory_gb)
            if isinstance(self.max_temperature_c, bool) or self.max_temperature_c is not None:
                if self.max_temperature_c < 0:
                    raise ValueError("max_temperature_c must be non-negative.")
        else:
            raise ValueError("task_id must be non-empty.")

    @staticmethod
    def _validate_non_negative(label: str, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label} must be a number.")
        if value < 0:
            raise ValueError(f"{label} must be non-negative.")


# ---------------------------------------------------------------------- #
# Promotion / transition contract                                       #
# ---------------------------------------------------------------------- #

@dataclass(frozen=False)
class PromotionContract:
    """Explicit proof that an agent may enter a restricted state."""

    from_id: str
    to_id: str
    requester: str
    basis: str
    scope: str
    authorized_by: str
    timestamp: datetime
    nonce: str

    def __post_init__(self) -> None:
        for field_name in ("from_id", "to_id", "requester", "basis", "scope", "authorized_by", "nonce"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Promotion contract {field_name} must be non-empty.")
        if not isinstance(self.timestamp, datetime):
            raise ValueError("timestamp must be a datetime.")

    def verify(self) -> bool:
        return bool(self.nonce and self.basis and self.authorized_by)


# ---------------------------------------------------------------------- #
# Result validation                                                     #
# ---------------------------------------------------------------------- #

class ResultContractError(ValueError):
    """A result failed contract validation."""


def validate_result(result: Mapping[str, Any]) -> ResultContract:
    if not isinstance(result, Mapping):
        raise ResultContractError("result must be a mapping.")
    try:
        return ResultContract.from_dict(result)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise ResultContractError(f"result does not match the contract: {exc}") from exc


# ---------------------------------------------------------------------- #
# Status coercion                                                       #
# ---------------------------------------------------------------------- #

def _coerce_status(value: str) -> str:
    """Return the canonical status object for a plain string."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("status must be a non-empty string.")
    return value.strip().lower()
