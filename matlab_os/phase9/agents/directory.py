"""Thread-safe agent directory for Phase 9 (staging only).

The directory owns three pieces of live state per agent:

* the immutable part of the :class:`AgentProfile` (copied on registration),
* the mutable live status, changed only through this class, and
* the number of in-flight tasks (``active_count``), which can never go below
  zero and is what the selector compares against ``max_concurrent_tasks``.

All public methods take a re-entrant lock and return defensive copies so a
caller cannot mutate directory state behind the lock.
"""

from __future__ import annotations

import copy
import threading
from typing import Any

from ..errors import AgentNotFoundError
from ..security.redaction import redact
from .capability import AgentProfile, AgentStatus

_BUSY_STATUSES: frozenset[AgentStatus] = frozenset(
    {AgentStatus.AVAILABLE, AgentStatus.DEGRADED}
)


class AgentDirectory:
    """In-memory registry of agent profiles with live load tracking."""

    def __init__(self) -> None:
        self._profiles: dict[str, AgentProfile] = {}
        self._active: dict[str, int] = {}
        self._lock = threading.RLock()

    # -- registration ------------------------------------------------------- #
    def register(self, profile: AgentProfile) -> None:
        """Register *profile*.

        Raises:
            ValueError: when *profile* is not an :class:`AgentProfile` or its
                ``agent_id`` is already registered.
        """
        if not isinstance(profile, AgentProfile):
            raise ValueError("profile must be an AgentProfile instance.")
        with self._lock:
            if profile.agent_id in self._profiles:
                raise ValueError(
                    f"Agent already registered: {profile.agent_id!r}"
                )
            self._profiles[profile.agent_id] = copy.deepcopy(profile)
            self._active[profile.agent_id] = 0

    def unregister(self, agent_id: str) -> None:
        """Remove *agent_id* and its load counter.

        Raises:
            AgentNotFoundError: when the agent is not registered.
        """
        with self._lock:
            self._require(agent_id)
            del self._profiles[agent_id]
            self._active.pop(agent_id, None)

    # -- reads -------------------------------------------------------------- #
    def get(self, agent_id: str) -> AgentProfile:
        """Return a defensive copy of the registered profile."""
        with self._lock:
            return copy.deepcopy(self._require(agent_id))

    def all(self) -> list[AgentProfile]:
        """Return every profile as a defensive copy, ordered by ``agent_id``."""
        with self._lock:
            return [
                copy.deepcopy(self._profiles[agent_id])
                for agent_id in sorted(self._profiles)
            ]

    def active_count(self, agent_id: str) -> int:
        """Return the number of in-flight tasks for *agent_id*."""
        with self._lock:
            self._require(agent_id)
            return self._active.get(agent_id, 0)

    # -- live state --------------------------------------------------------- #
    def set_status(self, agent_id: str, status: AgentStatus | str) -> AgentProfile:
        """Set the live status explicitly and return the updated copy."""
        with self._lock:
            profile = self._require(agent_id)
            profile.status = self._coerce_status(status)
            return copy.deepcopy(profile)

    def mark_busy(self, agent_id: str) -> None:
        """Record one additional in-flight task.

        An ``AVAILABLE`` or ``DEGRADED`` agent becomes ``BUSY``; a
        ``DISABLED``/``UNAVAILABLE`` agent keeps its status.  The count is not
        capped here: the selector rejects agents whose count reaches
        ``max_concurrent_tasks``.
        """
        with self._lock:
            profile = self._require(agent_id)
            self._active[agent_id] = self._active.get(agent_id, 0) + 1
            if profile.status in _BUSY_STATUSES:
                profile.status = AgentStatus.BUSY

    def mark_available(self, agent_id: str) -> None:
        """Record one fewer in-flight task.

        The counter is clamped at zero (it must never go negative).  A
        ``BUSY`` agent with no remaining tasks returns to ``AVAILABLE``;
        explicitly ``DISABLED``/``UNAVAILABLE`` statuses are preserved.
        """
        with self._lock:
            profile = self._require(agent_id)
            remaining = max(0, self._active.get(agent_id, 0) - 1)
            self._active[agent_id] = remaining
            if remaining == 0 and profile.status is AgentStatus.BUSY:
                profile.status = AgentStatus.AVAILABLE

    def snapshot(self) -> list[dict[str, Any]]:
        """Return a deterministic, JSON-safe inventory of every agent."""
        with self._lock:
            return [
                {
                    "agent_id": profile.agent_id,
                    "display_name": profile.display_name,
                    "status": profile.status.value,
                    "enabled": profile.enabled,
                    "locality": profile.locality,
                    "max_complexity": profile.max_complexity,
                    "max_concurrent_tasks": profile.max_concurrent_tasks,
                    "active_count": self._active.get(profile.agent_id, 0),
                    "capabilities": sorted(profile.capabilities),
                    "tools": sorted(profile.tools),
                    "permission_scope": sorted(profile.permission_scope),
                    "cost_weight": profile.cost_weight,
                    "metadata": redact(dict(profile.metadata)),
                }
                for profile in (
                    self._profiles[agent_id] for agent_id in sorted(self._profiles)
                )
            ]

    # -- dunder helpers ----------------------------------------------------- #
    def __len__(self) -> int:
        with self._lock:
            return len(self._profiles)

    def __contains__(self, agent_id: object) -> bool:
        if not isinstance(agent_id, str):
            return False
        with self._lock:
            return agent_id in self._profiles

    # -- internals ---------------------------------------------------------- #
    def _require(self, agent_id: str) -> AgentProfile:
        """Return the stored profile or raise :class:`AgentNotFoundError`."""
        if not isinstance(agent_id, str):
            raise AgentNotFoundError(
                "agent_id must be a string.",
                agent_id=repr(agent_id),
            )
        profile = self._profiles.get(agent_id)
        if profile is None:
            raise AgentNotFoundError(
                f"Unknown agent: {agent_id!r}", agent_id=agent_id
            )
        return profile

    @staticmethod
    def _coerce_status(status: AgentStatus | str) -> AgentStatus:
        if isinstance(status, AgentStatus):
            return status
        if isinstance(status, str):
            try:
                return AgentStatus(status.strip().lower())
            except ValueError as exc:
                raise ValueError(f"Unknown agent status: {status!r}") from exc
        raise ValueError("status must be an AgentStatus or its string value.")


__all__ = ["AgentDirectory"]
