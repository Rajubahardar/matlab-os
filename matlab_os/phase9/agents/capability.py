"""Capability and profile model for Phase 9 agents (staging only).

This module is the single source of truth for *what* an agent declares it can do
and *under which bounds*.  It is pure data plus validation: it performs no I/O,
holds no locks and has no dependency on the directory, selector or collaboration
layers.

Validation policy
-----------------
Programmer mistakes (empty identifiers, non-positive budgets, unknown statuses)
raise ``ValueError``.  Domain failures in the other agent modules use the typed
``matlab_os.phase9.errors`` hierarchy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: The only supported agent localities.
LOCALITIES: frozenset[str] = frozenset({"local", "remote"})


class AgentStatus(str, Enum):
    """Live availability of a registered agent.

    Semantics used by the selector:

    * ``AVAILABLE``, ``BUSY`` and ``DEGRADED`` remain selectable while the
      agent has free capacity.
    * ``UNAVAILABLE`` and ``DISABLED`` are hard-filtered out.
    """

    AVAILABLE = "available"
    BUSY = "busy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"

    def __str__(self) -> str:
        return self.value


def _require_text(value: Any, label: str) -> str:
    """Return *value* when it is a non-blank string, else raise ``ValueError``."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value


def _require_positive_int(value: Any, label: str) -> int:
    """Return *value* when it is an int >= 1 (``bool`` is rejected)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be an integer >= 1.")
    return value


def _require_name_set(values: Any, label: str) -> frozenset[str]:
    """Normalise an iterable of non-empty strings into a ``frozenset``."""
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ValueError(f"{label} must be an iterable of non-empty strings.")
    collected: set[str] = set()
    for item in values:
        collected.add(_require_text(item, f"{label} entry"))
    return frozenset(collected)


def _coerce_status(value: Any) -> AgentStatus:
    """Coerce an enum or its string value into :class:`AgentStatus`."""
    if isinstance(value, AgentStatus):
        return value
    if isinstance(value, str):
        try:
            return AgentStatus(value.strip().lower())
        except ValueError as exc:
            raise ValueError(f"Unknown agent status: {value!r}") from exc
    raise ValueError("status must be an AgentStatus or its string value.")


@dataclass(frozen=True)
class AgentCapability:
    """One named thing an agent can do.

    Attributes:
        name: unique capability name (for example ``"code"`` or ``"review"``).
        tools: concrete tool identifiers the capability relies on.
        max_complexity: largest task complexity this capability supports.
        description: optional human-readable description.
    """

    name: str
    tools: tuple[str, ...] = ()
    max_complexity: int = 1
    description: str = ""

    def __post_init__(self) -> None:
        _require_text(self.name, "AgentCapability.name")
        if isinstance(self.tools, (str, bytes)):
            raise ValueError(
                "AgentCapability.tools must be an iterable of strings, "
                "not a bare string."
            )
        tools: list[str] = []
        for tool in self.tools:
            tools.append(_require_text(tool, "AgentCapability.tools entry"))
        object.__setattr__(self, "tools", tuple(tools))
        _require_positive_int(
            self.max_complexity, "AgentCapability.max_complexity"
        )
        if not isinstance(self.description, str):
            raise ValueError("AgentCapability.description must be a string.")

    def supports_complexity(self, complexity: int) -> bool:
        """Return True when *complexity* is within this capability's bound."""
        return complexity <= self.max_complexity

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tools": list(self.tools),
            "max_complexity": self.max_complexity,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentCapability":
        if not isinstance(payload, Mapping):
            raise ValueError("AgentCapability payload must be a mapping.")
        if "name" not in payload:
            raise ValueError("AgentCapability payload is missing 'name'.")
        tools = payload.get("tools", ())
        if isinstance(tools, (str, bytes)) or not isinstance(tools, Iterable):
            raise ValueError(
                "AgentCapability.tools must be an iterable of strings."
            )
        return cls(
            name=payload["name"],
            tools=tuple(tools),
            max_complexity=payload.get("max_complexity", 1),
            description=payload.get("description", ""),
        )


@dataclass
class AgentProfile:
    """Declared identity, capabilities and budgets of one agent.

    The profile is mutable so the directory can update the live status in one
    place, but the directory hands out defensive copies.  ``to_dict`` redacts
    ``metadata`` so a serialised profile can never leak a credential.
    """

    agent_id: str
    display_name: str = ""
    capabilities: Mapping[str, AgentCapability] = field(default_factory=dict)
    tools: frozenset[str] = frozenset()
    permission_scope: frozenset[str] = frozenset()
    max_complexity: int = 1
    max_concurrent_tasks: int = 1
    locality: str = "local"
    status: AgentStatus = AgentStatus.AVAILABLE
    enabled: bool = True
    cost_weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.agent_id, "AgentProfile.agent_id")
        if not isinstance(self.display_name, str):
            raise ValueError("AgentProfile.display_name must be a string.")

        if not isinstance(self.capabilities, Mapping):
            raise ValueError("AgentProfile.capabilities must be a mapping.")
        normalized: dict[str, AgentCapability] = {}
        for name, capability in self.capabilities.items():
            _require_text(name, "AgentProfile.capabilities key")
            if not isinstance(capability, AgentCapability):
                raise ValueError(
                    "AgentProfile.capabilities values must be "
                    "AgentCapability instances."
                )
            if capability.name != name:
                raise ValueError(
                    f"AgentProfile capability key {name!r} does not match "
                    f"capability name {capability.name!r}."
                )
            normalized[name] = capability
        self.capabilities = normalized

        self.tools = _require_name_set(self.tools, "AgentProfile.tools")
        self.permission_scope = _require_name_set(
            self.permission_scope, "AgentProfile.permission_scope"
        )
        _require_positive_int(self.max_complexity, "AgentProfile.max_complexity")
        _require_positive_int(
            self.max_concurrent_tasks, "AgentProfile.max_concurrent_tasks"
        )
        if self.locality not in LOCALITIES:
            raise ValueError("AgentProfile.locality must be 'local' or 'remote'.")
        self.status = _coerce_status(self.status)
        if not isinstance(self.enabled, bool):
            raise ValueError("AgentProfile.enabled must be a bool.")
        if (
            isinstance(self.cost_weight, bool)
            or not isinstance(self.cost_weight, (int, float))
            or self.cost_weight <= 0
        ):
            raise ValueError(
                "AgentProfile.cost_weight must be a number greater than 0."
            )
        self.cost_weight = float(self.cost_weight)
        if not isinstance(self.metadata, Mapping):
            raise ValueError("AgentProfile.metadata must be a mapping.")
        self.metadata = dict(self.metadata)

    # -- capability queries ------------------------------------------------ #
    def has_capability(self, capability: str) -> bool:
        """Return True when the capability is declared."""
        return isinstance(capability, str) and capability in self.capabilities

    def missing_capabilities(self, required: Iterable[str]) -> list[str]:
        """Return the sorted capabilities in *required* that are absent."""
        return sorted(
            name for name in set(required) if not self.has_capability(name)
        )

    def missing_tools(self, required: Iterable[str]) -> list[str]:
        """Return the sorted tools in *required* that the profile lacks."""
        return sorted(set(required) - self.tools)

    def missing_permissions(self, required: Iterable[str]) -> list[str]:
        """Return the sorted permissions in *required* that are absent."""
        return sorted(set(required) - self.permission_scope)

    def effective_max_complexity(
        self, required_capabilities: Iterable[str] = ()
    ) -> int:
        """Return the effective complexity budget for a set of capabilities.

        The budget is the smallest of the profile-level bound and every matched
        capability-level bound; unmatched capabilities are ignored here (the
        selector rejects them before this point).
        """
        limit = self.max_complexity
        for name in required_capabilities:
            capability = self.capabilities.get(name)
            if capability is not None:
                limit = min(limit, capability.max_complexity)
        return limit

    # -- serialisation ------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        from ..security.redaction import redact

        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "capabilities": {
                name: capability.to_dict()
                for name, capability in sorted(self.capabilities.items())
            },
            "tools": sorted(self.tools),
            "permission_scope": sorted(self.permission_scope),
            "max_complexity": self.max_complexity,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "locality": self.locality,
            "status": self.status.value,
            "enabled": self.enabled,
            "cost_weight": self.cost_weight,
            "metadata": redact(dict(self.metadata)),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentProfile":
        if not isinstance(payload, Mapping):
            raise ValueError("AgentProfile payload must be a mapping.")
        if "agent_id" not in payload:
            raise ValueError("AgentProfile payload is missing 'agent_id'.")
        raw_capabilities = payload.get("capabilities", {})
        if not isinstance(raw_capabilities, Mapping):
            raise ValueError("AgentProfile.capabilities must be a mapping.")
        capabilities: dict[str, AgentCapability] = {}
        for name, value in raw_capabilities.items():
            capabilities[name] = (
                value
                if isinstance(value, AgentCapability)
                else AgentCapability.from_dict(value)
            )
        return cls(
            agent_id=payload["agent_id"],
            display_name=payload.get("display_name", ""),
            capabilities=capabilities,
            tools=payload.get("tools", frozenset()),
            permission_scope=payload.get("permission_scope", frozenset()),
            max_complexity=payload.get("max_complexity", 1),
            max_concurrent_tasks=payload.get("max_concurrent_tasks", 1),
            locality=payload.get("locality", "local"),
            status=payload.get("status", AgentStatus.AVAILABLE),
            enabled=payload.get("enabled", True),
            cost_weight=payload.get("cost_weight", 1.0),
            metadata=payload.get("metadata", {}),
        )


__all__ = [
    "LOCALITIES",
    "AgentStatus",
    "AgentCapability",
    "AgentProfile",
]
