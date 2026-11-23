"""Authoritative agent registry for the Phase 12 agent ecosystem.

This registry is the only place agent identity, lifecycle state and version
compatibility are stored.  It does not decide policy: permissions and approvals
are enforced by the reused :class:`matlab_os.orchestration.permissions
.PermissionManager` and the approval gates.  Registration does not imply that
an agent is operational.

State model
-----------
* registered  - profile accepted and stored (config present)
* initialized - register + initialize() completed
* ready       - dependencies validated and agent may be selected
* running     - agent has an active dispatch (or has accepted one)
* paused      - explicitly suspended
* failed      - state that can be recovered by a bounded recovery step
* stopped     - terminal state; no new dispatch

Untracked runtime registries such as process-list or process-ID caches are
deliberately excluded: they are temporary plumbing and can race with the
authoritative registry.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from matlab_os.phase9.agents.capability import AgentProfile, AgentStatus

from matlab_os.integration.agent_ecosystem import contract as ec

#: Legal transitions.  Writes are serialized by the registry lock.
LEGACY_TRANSITIONS: dict[str, frozenset[str]] = {
    "registered": frozenset({"initialized", "failed", "stopped"}),
    "initialized": frozenset({"ready", "failed", "paused", "stopped"}),
    "ready": frozenset({"running", "paused", "failed", "stopped"}),
    "running": frozenset({"paused", "failed", "stopped"}),
    "paused": frozenset({"ready", "failed", "stopped"}),
    "failed": frozenset({"ready", "stopped"}),
    "stopped": frozenset(),
}


class RegistryError(ValueError):
    """The registry itself cannot be used as configured."""


class DuplicateRegistrationError(RegistryError):
    """An agent with the same agent_id is already registered."""


class InvalidStateTransitionError(RegistryError):
    """The requested lifecycle transition is not legal."""


class ReservedAgentIdError(RegistryError):
    """The requested agent_id is reserved for a core component."""


class VersionCompatibilityError(RegistryError):
    """The current profile is not compatible with the requested version."""


class MissingDependenciesError(RegistryError):
    """Required tools, models or permissions are not available."""


class Registry:
    """Single authoritative agent registry for the ecosystem."""

    #: Agent ids owned by core components.  They cannot be claimed as users.
    CORE_RESERVED = frozenset({
        "phase9-planner",
        "phase9-verifier",
        "phase9-research",
        "matrix-runner",
    })

    def __init__(
        self,
        *,
        state_root: Path | None = None,
        audit: Any | None = None,
        clock: Any | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._clock = clock or datetime.now(timezone.utc)
        self._audit = audit
        self._state_root = Path(state_root) if state_root is not None else Path("data/agent_ecosystem_state")
        self._state_root.mkdir(parents=True, exist_ok=True)

        # id -> AgentProfile (defensive copy only during touches)
        self._profiles: dict[str, AgentProfile] = {}
        # id -> lifecycle state
        self._states: dict[str, str] = {}
        # id -> registration time
        self._registrations: dict[str, datetime] = {}
        # id -> last version declared
        self._versions: dict[str, str] = {}
        self._closed = False

    # ------------------------------------------------------------------ #
    # Public operations                                                 #
    # ------------------------------------------------------------------ #

    def register(self, profile: AgentProfile, *, metadata: Mapping[str, Any] | None = None) -> str:
        """Register a profile.

        Returns the stable registration id.  Fails if the id is reserved,
        duplicate, or the profile is not compatible with the current runtime.
        """
        with self._lock:
            self._require_not_closed()
            self._validate_registration(profile, metadata)
            if profile.agent_id in self._profiles:
                raise DuplicateRegistrationError(f"agent already registered: {profile.agent_id!r}")

            self._profiles[profile.agent_id] = profile
            self._states[profile.agent_id] = "registered"
            self._registrations[profile.agent_id] = self._clock if isinstance(self._clock, datetime) else datetime.now(timezone.utc)
            self._versions[profile.agent_id] = str(metadata.get("version", "0.1.0")) if metadata else str(profile.version) if hasattr(profile, 'version') else "0.1.0"

            self._emit("registered", agent_id=profile.agent_id)
            return profile.agent_id

    def validate(self, agent_id: str, *, required_tools: Iterable[str] | None = None,
                 required_capabilities: Iterable[str] | None = None,
                 required_permissions: Iterable[str] | None = None,
                 resource_requirements: ec.ResourceRequirements | None = None) -> dict[str, Any]:
        """Validate that an agent can be made ready and return the check report."""
        with self._lock:
            self._require_not_closed()
            profile = self._require_agent(agent_id)
            if        self._states.get(agent_id) == "stopped":
                raise RegistryError(f"cannot validate agent in terminal state {self._states[agent_id]!r}.")

            report = {
                "agent_id": agent_id,
                "checked_at": self._clock.isoformat(),
                "status": self._states[agent_id],
                "missing_capabilities": profile.missing_capabilities(required_capabilities or ()),
                "missing_tools": profile.missing_tools(required_tools or ()),
                "missing_permissions": profile.missing_permissions(required_permissions or ()),
                "version": self._versions[agent_id],
                "resource_ok": True,
                "problems": [],
            }

            # Dependency validation is explicit, not automatic.
            if required_tools:
                missing_tools = report["missing_tools"]
                if missing_tools:
                    report["resource_ok"] = False
                    report["problems"].append(f"missing tools: {missing_tools}")
            if required_capabilities:
                missing_caps = report["missing_capabilities"]
                if missing_caps:
                    report["resource_ok"] = False
                    report["problems"].append(f"missing capabilities: {missing_caps}")
            if required_permissions:
                missing_permissions = report["missing_permissions"]
                if missing_permissions:
                    report["problems"].append(f"missing permissions: {missing_permissions}")

            # Resource check is performed only when a resource profile is supplied.
            if resource_requirements is not None:
                # Resource admission is delegated to the reused governor.
                report["resource_ok"] = True
                report["problems"].append("resource check deferred to ResourceGovernor")

            return report

    def initialize(self, agent_id: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        """Initialize an agent (for backward compatibility with test)."""
        with self._lock:
            self._require_not_closed()
            self._require_agent(agent_id)
            current = self._states.get(agent_id, "registered")
            if current not in {"registered", "initialized"}:
                raise RegistryError(f"cannot initialize {agent_id!r} in state {current!r}.")
            self._states[agent_id] = "initialized"
            self._emit("initialized", agent_id=agent_id, **metadata or {})
            self._emit("initialized", agent_id=agent_id, **metadata or {})

    def discover(self) -> list[dict[str, Any]]:
        """Return every registered agent as a serialised profile."""
        with self._lock:
            return [
                {
                    "agent_id": profile.agent_id,
                    "display_name": profile.display_name,
                    "status": self._states.get(profile.agent_id, "registered"),
                    "enabled": profile.enabled,
                    "capabilities": sorted(profile.capabilities),
                    "tools": sorted(profile.tools),
                    "permission_scope": sorted(profile.permission_scope),
                    "max_concurrent_tasks": profile.max_concurrent_tasks,
                    "max_complexity": profile.max_complexity,
                    "locality": profile.locality,
                    "registration_time": self._registrations.get(profile.agent_id, datetime.now(timezone.utc)).isoformat(),
                    "version": self._versions[profile.agent_id],
                }
                for profile in sorted(self._profiles.values(), key=lambda p: p.agent_id)
            ]

    def get(self, agent_id: str) -> AgentProfile:
        with self._lock:
            self._require_not_closed()
            profile = self._require_agent(agent_id)
            return AgentProfile(
                agent_id=profile.agent_id,
                display_name=profile.display_name,
                capabilities=profile.capabilities,
                tools=tuple(profile.tools),
                permission_scope=tuple(profile.permission_scope),
                max_complexity=profile.max_complexity,
                max_concurrent_tasks=profile.max_concurrent_tasks,
                locality=profile.locality,
                status=profile.status,
                enabled=profile.enabled,
            )

    def lookup_by_capability(self, capability: str) -> list[str]:
        """Return agent ids that declare *capability*."""
        with self._lock:
            self._require_not_closed()
            return [
                agent_id for agent_id, profile in self._profiles.items()
                if profile.has_capability(capability)
            ]

    def query_by_capability(self, capability: str) -> list[str]:
        return self.lookup_by_capability(capability)



    def discover(self) -> list[dict[str, Any]]:
        """Return every registered agent as a serialised profile."""
        with self._lock:
            return [
                {
                    "agent_id": profile.agent_id,
                    "display_name": profile.display_name,
                    "status": self._states.get(profile.agent_id, "registered"),
                    "enabled": profile.enabled,
                    "capabilities": sorted(profile.capabilities),
                    "tools": sorted(profile.tools),
                    "permission_scope": sorted(profile.permission_scope),
                    "max_concurrent_tasks": profile.max_concurrent_tasks,
                    "max_complexity": profile.max_complexity,
                    "locality": profile.locality,
                    "registration_time": self._registrations.get(profile.agent_id, datetime.now(timezone.utc)).isoformat(),
                    "version": self._versions[profile.agent_id],
                }
                for profile in sorted(self._profiles.values(), key=lambda p: p.agent_id)
            ]

    def update_status(self, agent_id: str, status: str) -> None:
        """Explicitly set one lifecycle state."""
        with self._lock:
            self._require_not_closed()
            profile = self._require_agent(agent_id)
            new_status = ec._coerce_status(status)
            current = self._states.get(agent_id, "registered")
            if new_status == current:
                # Recording the same state twice is not a transition; the
                # lifecycle manager and registry both persist lifecycle steps.
                return
            if new_status not in LEGACY_TRANSITIONS.get(current, frozenset()):
                raise InvalidStateTransitionError(
                    f"cannot move {agent_id} from {self._states[agent_id]!r} to {new_status!r}"
                )
            previous = self._states[agent_id]
            self._states[agent_id] = new_status
            self._emit("status_changed", agent_id=agent_id, from_status=previous, to_status=new_status)

    def unregister(self, agent_id: str) -> None:
        with self._lock:
            self._require_not_closed()
            if agent_id not in self._profiles:
                raise KeyError(f"unknown agent: {agent_id}")
            if self._states.get(agent_id) in {"failed", "stopped"}:
                raise RegistryError(f"cannot unregister a terminal-state agent.")
            del self._profiles[agent_id]
            del self._states[agent_id]
            del self._registrations[agent_id]
            del self._versions[agent_id]
            self._emit("unregistered", agent_id=agent_id)

    def version_compatibility(self, agent_id: str, requested: str) -> dict[str, Any]:
        with self._lock:
            self._require_not_closed()
            profile = self._require_agent(agent_id)
            current = self._versions.get(agent_id, "0.1.0")
            compatible = self._is_version_compatible(current, requested)
            return {
                "agent_id": agent_id,
                "current": current,
                "requested": requested,
                "compatible": compatible,
                "problems": [] if compatible else ["version mismatch"],
            }

    def duplicate_id_detection(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        """Return duplicate-ID report for a candidate profile."""
        with self._lock:
            self._require_not_closed()
            agent_id = str(candidate.get("agent_id", ""))
            if agent_id in self._profiles:
                return {"agent_id": agent_id, "duplicate": True, "existing": self.get(agent_id)}
            return {"agent_id": agent_id, "duplicate": False}

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.discover())

    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._profiles.clear()
            self._states.clear()
            self._registrations.clear()
            self._versions.clear()
            self._emit("closed")

    # ------------------------------------------------------------------ #
    # Internals                                                         #
    # ------------------------------------------------------------------ #

    def _require_not_closed(self) -> None:
        if self._closed:
            raise RegistryError("registry is closed.")

    @staticmethod
    def _is_version_compatible(current: str, requested: str) -> bool:
        current_parts = [int(part) for part in str(current).split(".") if part.isdigit()]
        requested_parts = [int(part) for part in str(requested).split(".") if part.isdigit()]
        return current_parts[:2] == requested_parts[:2]

    def _emit(self, event: str, **details: Any) -> None:
        if self._audit is not None:
            # Phase 9 audit sinks take an explicit actor/outcome; the registry
            # never decides policy, so its own events are always "allowed".
            self._audit.record(
                action=f"agent.registry.{event}",
                actor="agent-ecosystem",
                outcome="allowed",
                details=dict(details),
            )

    def _validate_registration(self, profile: AgentProfile, metadata: Mapping[str, Any] | None) -> None:
        if not isinstance(profile, AgentProfile):
            raise TypeError("agent must be an AgentProfile.")

        if profile.agent_id in self.CORE_RESERVED:
            raise ReservedAgentIdError(f"agent_id reserved: {profile.agent_id!r}")

        if not profile.enabled:
            raise RegistryError(f"agent is disabled: {profile.agent_id!r}")

        current = self._versions.get(profile.agent_id, "0.1.0")
        if not self._is_version_compatible(current, metadata.get("version", "0.1.0") if metadata else "0.1.0"):
            raise VersionCompatibilityError(
                f"version incompatible: {profile.agent_id} is {current}, requested {metadata.get('version', '0.1.0')}"
            )

        # Dependency validation is deferred to validate(); this layer reports
        # missing capabilities/tools/permissions but never calls the registry.
        # A missing dependency is not an error at registration time.

    def _require_agent(self, agent_id: str) -> AgentProfile:
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_id must be a non-empty string.")
        profile = self._profiles.get(agent_id)
        if profile is None:
            raise KeyError(f"unknown agent: {agent_id}")
        return profile
