"""Phase 9 permission policy.

Two independent layers are enforced for every executed step:

1. **Exact-match grant** (reused from the stable main project,
   :class:`matlab_os.orchestration.permissions.PermissionManager`):
   a ``(agent_id, capability, skill, operation)`` tuple must have been granted.
   The default posture is deny — the manager is constructed with
   ``default_allow=False``.
2. **Declared permission scope**: the agent profile's ``permission_scope`` must
   be a superset of the permissions a capability requires.  An agent may hold a
   grant for ``implementation`` but still be refused if it never declared
   ``workspace.write``.

Both layers must pass.  A failure raises :class:`PermissionDeniedError` and is
audited; there is no code path that downgrades a denial to a warning.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, Mapping, Optional

from ..audit import AuditLog, NullAuditLog
from ..errors import PermissionDeniedError

#: Permission tokens a step of a given capability requires from its agent.
CAPABILITY_PERMISSIONS: dict[str, frozenset[str]] = {
    "analysis": frozenset(),
    "research": frozenset({"model.invoke"}),
    "design": frozenset(),
    "implementation": frozenset({"workspace.write"}),
    "simulation": frozenset({"workspace.read"}),
    "optimization": frozenset({"workspace.read"}),
    "debugging": frozenset({"workspace.read", "workspace.write"}),
    "verification": frozenset({"workspace.read"}),
    "documentation": frozenset({"workspace.write"}),
}

#: Permission tokens Phase 9 recognises at all.
KNOWN_PERMISSIONS: frozenset[str] = frozenset(
    {"workspace.read", "workspace.write", "model.invoke"}
)

PHASE9_SKILL = "phase9.step"
PHASE9_OPERATION = "execute"


def required_permissions(capability: str) -> frozenset[str]:
    """Return the permission tokens *capability* requires."""
    return CAPABILITY_PERMISSIONS.get(capability, frozenset())


def default_permission_manager():
    """Build the reused exact-match permission manager, deny-by-default.

    The import is local so that this module stays importable when the main
    project is unavailable (for example in an isolated review).
    """
    from matlab_os.orchestration.permissions import PermissionManager

    return PermissionManager(default_allow=False)


class PolicyEngine:
    """Authorisation for Phase 9 step execution."""

    def __init__(
        self,
        directory: Any,
        *,
        permission_manager: Any | None = None,
        audit: Optional[AuditLog] = None,
    ) -> None:
        self.directory = directory
        self.audit = audit or NullAuditLog()
        self._lock = threading.RLock()
        if permission_manager is None:
            try:
                permission_manager = default_permission_manager()
            except Exception:  # pragma: no cover - main project unavailable
                permission_manager = None
        self.permissions = permission_manager
        self._fallback_grants: set[tuple[str, str]] = set()

    # -- grants ------------------------------------------------------------ #
    def grant(self, agent_id: str, capability: str) -> None:
        self._validate(agent_id, capability)
        with self._lock:
            if self.permissions is not None:
                self.permissions.grant(
                    agent_id, capability, PHASE9_SKILL, PHASE9_OPERATION
                )
            else:  # pragma: no cover - defensive fallback
                self._fallback_grants.add((agent_id, capability))
        self._audit("permission.granted", "granted", {
            "agent_id": agent_id,
            "capability": capability,
        })

    def revoke(self, agent_id: str, capability: str) -> bool:
        self._validate(agent_id, capability)
        with self._lock:
            if self.permissions is not None:
                removed = bool(
                    self.permissions.revoke(
                        agent_id, capability, PHASE9_SKILL, PHASE9_OPERATION
                    )
                )
            else:  # pragma: no cover - defensive fallback
                existed = (agent_id, capability) in self._fallback_grants
                self._fallback_grants.discard((agent_id, capability))
                removed = existed
        self._audit(
            "permission.revoked", "revoked" if removed else "not_found",
            {"agent_id": agent_id, "capability": capability},
        )
        return removed

    def is_granted(self, agent_id: str, capability: str) -> bool:
        with self._lock:
            if self.permissions is not None:
                return bool(
                    self.permissions.is_allowed(
                        agent_id, capability, PHASE9_SKILL, PHASE9_OPERATION
                    )
                )
            return (agent_id, capability) in self._fallback_grants

    # -- enforcement ------------------------------------------------------- #
    def require(
        self,
        agent_id: str,
        capability: str,
        *,
        step_id: str = "",
        required: Optional[Iterable[str]] = None,
    ) -> None:
        """Raise unless both layers authorise this agent/capability pair."""
        needed = (
            frozenset(required) if required is not None
            else required_permissions(capability)
        )

        if not self.is_granted(agent_id, capability):
            self._audit("permission.denied", "denied", {
                "agent_id": agent_id,
                "capability": capability,
                "step_id": step_id,
                "reason": "grant_missing",
            })
            raise PermissionDeniedError(
                "No exact-match grant exists for this agent and capability.",
                agent_id=agent_id,
                capability=capability,
                skill=PHASE9_SKILL,
                operation=PHASE9_OPERATION,
            )

        try:
            profile = self.directory.get(agent_id)
        except Exception as exc:
            self._audit("permission.denied", "denied", {
                "agent_id": agent_id,
                "capability": capability,
                "reason": "unknown_agent",
            })
            raise PermissionDeniedError(
                "Agent is not registered in the Phase 9 directory.",
                agent_id=agent_id,
            ) from exc

        scope = set(getattr(profile, "permission_scope", frozenset()) or ())
        unknown = sorted(needed - KNOWN_PERMISSIONS)
        missing = sorted(needed - scope)
        if unknown or missing:
            self._audit("permission.denied", "denied", {
                "agent_id": agent_id,
                "capability": capability,
                "step_id": step_id,
                "missing": missing,
                "unknown": unknown,
            })
            raise PermissionDeniedError(
                "Agent lacks the permissions required by this capability.",
                agent_id=agent_id,
                capability=capability,
                missing=missing,
            )

    # -- introspection ----------------------------------------------------- #
    def matrix(self) -> dict[str, Any]:
        return {
            "capability_permissions": {
                capability: sorted(permissions)
                for capability, permissions in CAPABILITY_PERMISSIONS.items()
            },
            "known_permissions": sorted(KNOWN_PERMISSIONS),
            "skill": PHASE9_SKILL,
            "operation": PHASE9_OPERATION,
            "backend": (
                "matlab_os.orchestration.permissions.PermissionManager"
                if self.permissions is not None else "in-memory fallback"
            ),
        }

    def inventory(self) -> dict[str, Any]:
        if self.permissions is not None:
            grants = [
                {"grant": list(item)}
                for item in self.permissions.inventory()
            ]
            count = self.permissions.count()
        else:  # pragma: no cover - defensive fallback
            grants = [
                {"grant": [agent, capability, PHASE9_SKILL, PHASE9_OPERATION]}
                for agent, capability in sorted(self._fallback_grants)
            ]
            count = len(self._fallback_grants)
        return {"count": count, "grants": grants}

    # -- helpers ----------------------------------------------------------- #
    @staticmethod
    def _validate(agent_id: str, capability: str) -> None:
        for name, value in (("agent_id", agent_id), ("capability", capability)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string.")

    def _audit(self, action: str, outcome: str,
               details: Mapping[str, Any]) -> None:
        try:
            self.audit.record(
                action=action, actor="policy", outcome=outcome,
                details=dict(details),
            )
        except Exception:  # pragma: no cover - audit must not mask decisions
            pass


__all__ = [
    "CAPABILITY_PERMISSIONS",
    "KNOWN_PERMISSIONS",
    "PHASE9_SKILL",
    "PHASE9_OPERATION",
    "PolicyEngine",
    "default_permission_manager",
    "required_permissions",
]
