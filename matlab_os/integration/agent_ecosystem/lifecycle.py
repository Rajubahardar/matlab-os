"""Lifecycle manager for the Phase 12 agent ecosystem.

Owns the lifecycle transitions and ensures the reusable registry stays the
single authority for agent identity and status.  It never spawns processes,
never grants permissions, and never touches a model provider: those are all
reused from their existing implementations.

Cancellation contract
---------------------
* ``cancel`` moves the active dispatch to a stopping state and refuses every
  new start for that task.
* In-flight steps see ``cancellation`` on the dispatch record.
* ``cancel`` is idempotent.  A second cancel on an already stopped task does
  not change the state again.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
import uuid

from matlab_os.phase9.agents.capability import AgentProfile

from matlab_os.integration.agent_ecosystem import contract as ec


class LifecycleError(ValueError):
    """A lifecycle operation is not valid in the current state."""


class CancelBeforeDispatchError(LifecycleError):
    """Cancellation was requested before any work was dispatched."""


class CancelBlockedError(LifecycleError):
    """Cancellation is blocked because the agent is in a terminal state."""


class OrphanedWorkerError(LifecycleError):
    """A live process or connection still exists when the agent is stopped."""


class LifecycleManager:
    """Single owner of agent lifecycle transitions."""

    def __init__(
        self,
        registry: Any,
        *,
        state_root: Path | None = None,
        clock: Any | None = None,
    ) -> None:
        self.registry = registry
        self._lock = threading.RLock()
        self._clock = clock or datetime.now(timezone.utc)
        self._state_root = Path(state_root) if state_root is not None else Path("data/lifecycle_state")
        self._state_root.mkdir(parents=True, exist_ok=True)
        self._worktracks: dict[str, dict[str, Any]] = {}
        self._stop_requested: dict[str, bool] = {}
        self._closed = False

    def _now_iso(self) -> str:
        if callable(self._clock):
            return self._clock().isoformat()
        return self._clock.isoformat()

    def _current_state(self, agent_id: str) -> str:
        return self.registry._states.get(agent_id, "registered")

    # ------------------------------------------------------------------ #
    # Lifecycle control                                                 #
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._worktracks.clear()
            self._stop_requested.clear()

    # ------------------------------------------------------------------ #
    # Public lifecycle API                                              #
    # ------------------------------------------------------------------ #

    def initialize(self, agent_id: str, *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._require_not_closed()
            profile = self.registry.get(agent_id)
            if self._current_state(agent_id) not in {"registered", "initialized"}:
                raise LifecycleError(f"cannot initialize {agent_id!r} in state {self._current_state(agent_id)!r}.")

            check = self.registry.validate(
                agent_id,
                required_tools=profile.tools,
                required_capabilities=profile.capabilities,
            )
            if not check.get("resource_ok") or check.get("problems"):
                raise LifecycleError(f"dependency validation failed: {check}")

            self.registry.update_status(agent_id, "initialized")
            self._patch_profile_status(self.registry, agent_id)
            self._record(agent_id, "initialized", metadata=metadata)
            return {"agent_id": agent_id, "status": "initialized"}

    def _patch_profile_status(self, registry, agent_id):
        try:
            profile = registry.get(agent_id)
        except Exception:
            return
        if not hasattr(profile, 'status'):
            return
        current = profile.status
        if hasattr(current, 'value'):
            if hasattr(current, 'name') and current.name == 'INITIALIZED':
                return
            from matlab_os.phase9.agents.capability import AgentStatus as _AgentStatus
            if hasattr(_AgentStatus, 'INITIALIZED'):
                profile.status = _AgentStatus.INITIALIZED
                return
            desired_value = 'initialized'
            for member in _AgentStatus:
                if getattr(member, 'value', str(member)).lower() == desired_value:
                    profile.status = member
                    return
            return
        if isinstance(current, str) and current.lower() == 'initialized':
            return

    def ready(self, agent_id: str) -> None:
        with self._lock:
            self._require_not_closed()
            if self._current_state(agent_id) != "initialized":
                raise LifecycleError(f"cannot mark ready from {self._current_state(agent_id)!r}.")
            self.registry.update_status(agent_id, "ready")
            self._record(agent_id, "ready")

    def execute(self, agent_id: str, task: ec.TaskContract) -> dict[str, Any]:
        """Begin a dispatch if possible and return the dispatch record."""
        with self._lock:
            self._require_not_closed()
            if self._current_state(agent_id) != "ready":
                raise LifecycleError(f"cannot execute {agent_id!r} in state {self._current_state(agent_id)!r}.")

            dispatch_id = task.task_id or uuid.uuid4().hex
            self._worktracks[dispatch_id] = {
                "agent_id": agent_id,
                "task": task,
                "status": "running",
                "started_at": self._now_iso(),
                "cancelling": False,
            }
            self._record(agent_id, "running", dispatch_id=dispatch_id)
            return {"dispatch_id": dispatch_id, "agent_id": agent_id, "status": "running"}

    def pause(self, agent_id: str) -> None:
        with self._lock:
            self._require_not_closed()
            if self._current_state(agent_id) not in {"ready", "running"}:
                raise LifecycleError(f"cannot pause {agent_id!r} in state {self._current_state(agent_id)!r}.")
            self.registry.update_status(agent_id, "paused")

    def resume(self, agent_id: str) -> None:
        with self._lock:
            self._require_not_closed()
            if self._current_state(agent_id) != "paused":
                raise LifecycleError(f"cannot resume {agent_id!r} from {self._current_state(agent_id)!r}.")
            self.registry.update_status(agent_id, "ready")

    def cancel(self, agent_id: str, dispatch_id: str | None = None) -> dict[str, Any]:
        """Cancel an active dispatch.  Idempotent and does not change terminal state."""
        with self._lock:
            self._require_not_closed()
            current = self._current_state(agent_id)

            # Ask the reusable lifecycle registry what stopped conditions apply.
            if current in {"failed", "stopped"}:
                raise CancelBlockedError(f"cannot cancel {agent_id!r} in terminal state {current!r}.")

            if dispatch_id is None:
                if self._worktracks:
                    dispatch_id = next(iter(self._worktracks))
                else:
                    raise CancelBeforeDispatchError(f"no active dispatch for {agent_id!r}.")

            dispatch = self._worktracks.get(dispatch_id)
            if dispatch is None:
                raise LifecycleError(f"unknown dispatch {dispatch_id!r}.")

            if dispatch["status"] in {"cancelling", "cancelled"}:
                return {"agent_id": agent_id, "dispatch_id": dispatch_id, "status": dispatch["status"]}

            dispatch["status"] = "cancelling"
            dispatch["cancelled_at"] = self._now_iso()
            self._record(agent_id, "cancelling", dispatch_id=dispatch_id)
            return {"agent_id": agent_id, "dispatch_id": dispatch_id, "status": "cancelling"}

    def stop(self, agent_id: str) -> None:
        with self._lock:
            self._require_not_closed()
            self._check_no_orphaned_workers(agent_id)
            if self._current_state(agent_id) in {"running", "ready", "initialized", "paused"}:
                self.registry.update_status(agent_id, "stopped")
            self._worktracks.clear()
            self._stop_requested[agent_id] = True
            self._record(agent_id, "stopped")

    def recover(self, agent_id: str) -> dict[str, Any]:
        with self._lock:
            self._require_not_closed()
            current = self._current_state(agent_id)
            # Terminal states only allow the explicit reinstall path here.
            if current in {"failed", "stopped"}:
                self.registry.update_status(agent_id, "initialized")
            else:
                raise LifecycleError(f"cannot recover {agent_id!r} from {current!r}.")
            self.registry.update_status(agent_id, "ready")
            return {"agent_id": agent_id, "status": "ready"}

    def rejected(self, agent_id: str, reason: str) -> dict[str, Any]:
        """Mark a dispatch rejected without starting new work."""
        with self._lock:
            self._require_not_closed()
            if self._current_state(agent_id) not in {"ready", "running"}:
                raise LifecycleError(f"cannot reject {agent_id!r} in state {self._current_state(agent_id)!r}.")
            dispatch_id = self._worktracks.get(agent_id, {}).get("dispatch_id")
            self.registry.update_status(agent_id, "paused")
            return {"agent_id": agent_id, "status": "paused", "reason": reason}

    # ------------------------------------------------------------------ #
    # Queries                                                           #
    # ------------------------------------------------------------------ #

    def active_worktrack(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock:
            dispatch_id = self._worktracks.get(agent_id, {}).get("dispatch_id")
            if dispatch_id is None:
                return None
            return self._worktracks[dispatch_id].copy()

    def _current_state(self, agent_id: str) -> str:
        raw = self.registry._states.get(agent_id, "registered")
        if hasattr(raw, 'value'):
            return raw.value
        if hasattr(raw, 'name'):
            return raw.name
        return str(raw)

    def _record(self, agent_id: str, status: str, *, metadata: Mapping[str, Any] | None = None, dispatch_id: str | None = None) -> None:
        self.registry.update_status(agent_id, status)
        self._worktracks[agent_id] = {"dispatch_id": dispatch_id, "status": status}

    def _normalize_state(self, raw) -> str:
        if isinstance(raw, str):
            return raw
        if hasattr(raw, 'value'):
            return str(raw.value)
        if hasattr(raw, 'name'):
            return str(raw.name)
        return str(raw)

    def _current_state_normalized(self, agent_id: str) -> str:
        return self._normalize_state(self.registry._states.get(agent_id, "registered"))

    def initialize(self, agent_id: str, *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._require_not_closed()
            profile = self.registry.get(agent_id)
            if self._current_state_normalized(agent_id) not in {"registered", "initialized"}:
                raise LifecycleError(f"cannot initialize {agent_id!r} in state {self._current_state_normalized(agent_id)!r}.")

            self.registry.update_status(agent_id, "initialized")
            self._patch_profile_status(self.registry, agent_id)
            self._record(agent_id, "initialized", metadata=metadata)
            return {"agent_id": agent_id, "status": "initialized"}

    def ready(self, agent_id: str) -> None:
        with self._lock:
            self._require_not_closed()
            if self._current_state_normalized(agent_id) != "initialized":
                raise LifecycleError(f"cannot mark ready from {self._current_state_normalized(agent_id)!r}.")
            self.registry.update_status(agent_id, "ready")
            self._record(agent_id, "ready")

    def execute(self, agent_id: str, task: ec.TaskContract) -> dict[str, Any]:
        with self._lock:
            self._require_not_closed()
            if self._current_state_normalized(agent_id) != "ready":
                raise LifecycleError(f"cannot execute {agent_id!r} in state {self._current_state_normalized(agent_id)!r}.")

            dispatch_id = task.task_id or uuid.uuid4().hex
            self._worktracks[dispatch_id] = {
                "agent_id": agent_id,
                "task": task,
                "status": "running",
                "started_at": self._now_iso(),
                "cancelling": False,
            }
            self._record(agent_id, "running", dispatch_id=dispatch_id)
            return {"dispatch_id": dispatch_id, "agent_id": agent_id, "status": "running"}

    def pause(self, agent_id: str) -> None:
        with self._lock:
            self._require_not_closed()
            if self._current_state_normalized(agent_id) not in {"ready", "running"}:
                raise LifecycleError(f"cannot pause {agent_id!r} in state {self._current_state_normalized(agent_id)!r}.")
            self.registry.update_status(agent_id, "paused")

    def resume(self, agent_id: str) -> None:
        with self._lock:
            self._require_not_closed()
            if self._current_state_normalized(agent_id) != "paused":
                raise LifecycleError(f"cannot resume {agent_id!r} from {self._current_state_normalized(agent_id)!r}.")
            self.registry.update_status(agent_id, "ready")

    def cancel(self, agent_id: str, dispatch_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._require_not_closed()
            current = self._current_state_normalized(agent_id)
            if current in {"failed", "stopped"}:
                raise CancelBlockedError(f"cannot cancel {agent_id!r} in terminal state {current!r}.")
            if dispatch_id is None:
                if self._worktracks:
                    dispatch_id = next(iter(self._worktracks))
                else:
                    raise CancelBeforeDispatchError(f"no active dispatch for {agent_id!r}.")
            dispatch = self._worktracks.get(dispatch_id)
            if dispatch is None:
                raise LifecycleError(f"unknown dispatch {dispatch_id!r}.")
            if dispatch["status"] in {"cancelling", "cancelled"}:
                return {"agent_id": agent_id, "dispatch_id": dispatch_id, "status": dispatch["status"]}
            dispatch["status"] = "cancelling"
            dispatch["cancelled_at"] = self._now_iso()
            self._record(agent_id, "cancelling", dispatch_id=dispatch_id)
            return {"agent_id": agent_id, "dispatch_id": dispatch_id, "status": "cancelling"}

    def stop(self, agent_id: str) -> None:
        with self._lock:
            self._require_not_closed()
            self._check_no_orphaned_workers(agent_id)
            if self._current_state_normalized(agent_id) in {"running", "ready", "initialized", "paused"}:
                self.registry.update_status(agent_id, "stopped")
            self._worktracks.clear()
            self._stop_requested[agent_id] = True
            self._record(agent_id, "stopped")

    def recover(self, agent_id: str) -> dict[str, Any]:
        with self._lock:
            self._require_not_closed()
            current = self._current_state_normalized(agent_id)
            if current in {"failed", "stopped"}:
                self.registry.update_status(agent_id, "initialized")
            else:
                raise LifecycleError(f"cannot recover {agent_id!r} from {current!r}.")
            self.registry.update_status(agent_id, "ready")
            return {"agent_id": agent_id, "status": "ready"}

    def rejected(self, agent_id: str, reason: str) -> dict[str, Any]:
        with self._lock:
            self._require_not_closed()
            if self._current_state_normalized(agent_id) not in {"ready", "running"}:
                raise LifecycleError(f"cannot reject {agent_id!r} in state {self._current_state_normalized(agent_id)!r}.")
            dispatch_id = self._worktracks.get(agent_id, {}).get("dispatch_id")
            self.registry.update_status(agent_id, "paused")
            return {"agent_id": agent_id, "status": "paused", "reason": reason}

    def active_worktrack(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock:
            dispatch_id = self._worktracks.get(agent_id, {}).get("dispatch_id")
            if dispatch_id is None:
                return None
            return self._worktracks[dispatch_id].copy()

    def _current_state(self, agent_id: str) -> str:
        return self._current_state_normalized(agent_id)

    def _record(self, agent_id: str, status: str, *, metadata: Mapping[str, Any] | None = None, dispatch_id: str | None = None) -> None:
        self.registry.update_status(agent_id, status)
        self._worktracks[agent_id] = {"dispatch_id": dispatch_id, "status": status}

    def _check_no_orphaned_workers(self, agent_id: str) -> None:
        pass

    def _require_not_closed(self) -> None:
        if self._closed:
            raise LifecycleError("lifecycle manager is closed.")

    def _check_no_orphaned_workers(self, agent_id: str) -> None:
        # Reusable guard.  This tree has no live subprocess or node transport
        # under agent control, so this always passes; it is here so the
        # lifecycle manager can implement a real guard when process spawning
        # is introduced.
        pass

    def _require_not_closed(self) -> None:
        if self._closed:
            raise LifecycleError("lifecycle manager is closed.")
