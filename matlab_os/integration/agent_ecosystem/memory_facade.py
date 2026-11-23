"""Controlled memory facade over the existing SQLite service.

Reuses :class:`matlab_os.memory.database.DatabaseService` but adds task-scoped
context boundaries, provenance, and a no-silent-overwrite rule.

The facade never invents a second memory store: every read and write goes
through the project/experiment/evidence tables owned by ``DatabaseService``.
A task context is stored as one experiment inside a project named
``task:<task_id>``; the project is resolved by name so a separate facade
instance can still read what another one wrote.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from matlab_os.memory.database import DatabaseService

SOURCE_REFERENCE = "agent-ecosystem-memory"


class MemoryFacadeError(ValueError):
    """A memory operation is not valid."""


class PermissionDeniedError(MemoryFacadeError):
    """The actor may not perform this memory operation."""


class NotFoundError(MemoryFacadeError):
    """No context is stored for the requested task."""


class ConflictError(MemoryFacadeError):
    """Attempt to overwrite existing memory without permission."""


class MemoryFacade:
    """Controlled read/write view over the shared SQLite memory service."""

    def __init__(self, db: DatabaseService, *, workspace_root: Path | None = None) -> None:
        self.db = db
        self._workspace_root = (
            Path(workspace_root) if workspace_root is not None else Path("data/agent_ecosystem_memory")
        )
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # Public API                                                        #
    # ------------------------------------------------------------------ #

    def write_task_context(
        self,
        task_id: str,
        actor: str,
        context: Mapping[str, Any],
        *,
        permission: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        task_id = self._require_text(task_id, "task_id")
        actor = self._require_text(actor, "actor")
        if not isinstance(context, Mapping):
            raise ValueError("context must be a mapping.")

        with self._lock:
            self._require_permission(actor, "memory.write", task_id, "write", permission)

            existing = self._existing_experiment(task_id)
            if existing is not None:
                raise ConflictError(f"task context already exists for {task_id!r}.")

            record = {
                "task_id": task_id,
                "actor": actor,
                "context": dict(context),
                "written_at": datetime.now(timezone.utc).isoformat(),
                "provenance": [actor],
            }
            project_id = self._project_id(task_id, create=True)
            experiment_row_id = self.db.store_experiment(
                project_id=project_id,
                experiment_id=f"ctx:{task_id}",
                inputs=record,
                source_reference=f"{SOURCE_REFERENCE}:{task_id}",
            )
            self.db.store_evidence(
                experiment_row_id,
                claim=f"task context written for {task_id} by {actor}",
                observation="context stored in task-scoped experiment",
                source="agent_ecosystem.memory_facade",
                validated=True,
            )
            return {
                "task_id": task_id,
                "actor": actor,
                "project_id": project_id,
                "experiment_id": f"ctx:{task_id}",
                "experiment_row_id": experiment_row_id,
            }

    def read_task_context(
        self,
        task_id: str,
        actor: str,
        *,
        permission: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        task_id = self._require_text(task_id, "task_id")
        actor = self._require_text(actor, "actor")
        with self._lock:
            self._require_permission(actor, "memory.read", task_id, "read", permission)
            experiment = self._existing_experiment(task_id)
            if experiment is None:
                raise NotFoundError(f"no memory context for {task_id!r}.")
            inputs = dict(experiment["inputs"])
            return {
                "task_id": task_id,
                "actor": actor,
                "project_id": experiment["project_id"],
                "experiment_id": experiment["experiment_id"],
                "context": dict(inputs["context"]),
                "written_at": inputs.get("written_at"),
                "provenance": list(inputs.get("provenance", ())),
                "read_at": datetime.now(timezone.utc).isoformat(),
            }

    def read_all(self, actor: str, *, permission: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        actor = self._require_text(actor, "actor")
        with self._lock:
            self._require_permission(actor, "memory.read", "campaign-read-all", "read", permission)
            projects = self.db.conn.execute(
                "SELECT id, name FROM projects WHERE name LIKE 'task:%' ORDER BY id"
            ).fetchall()
            contexts: list[dict[str, Any]] = []
            for project_id, name in projects:
                task_id = name.split("task:", 1)[1]
                experiment = self._existing_experiment(task_id)
                if experiment is None:
                    continue
                contexts.append(
                    {
                        "task_id": task_id,
                        "project_id": project_id,
                        "experiment_id": experiment["experiment_id"],
                        "context": dict(experiment["inputs"]["context"]),
                        "read_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            return contexts

    def delete(
        self,
        task_id: str,
        actor: str,
        *,
        permission: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        task_id = self._require_text(task_id, "task_id")
        actor = self._require_text(actor, "actor")
        with self._lock:
            self._require_permission(actor, "memory.write", task_id, "write", permission)
            if self._existing_experiment(task_id) is None:
                raise NotFoundError(f"no memory context for {task_id!r}.")
            project_id = self._project_id(task_id, create=False)
            self.db.store_experiment(
                project_id=project_id,
                experiment_id=f"deleted:{task_id}",
                inputs={"status": "deleted", "actor": actor},
                source_reference=f"{SOURCE_REFERENCE}:{task_id}",
            )
            return {"task_id": task_id, "actor": actor, "project_id": project_id, "status": "deleted"}

    # ------------------------------------------------------------------ #
    # Internals                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _require_text(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string.")
        return value

    def _project_id(self, task_id: str, *, create: bool) -> int | None:
        name = f"task:{task_id}"
        project = self.db.get_project_by_name(name)
        if project is not None:
            return int(project["id"])
        if not create:
            return None
        return self.db.store_project(name=name, objectives=f"agent ecosystem context for {task_id}")

    def _existing_experiment(self, task_id: str) -> dict[str, Any] | None:
        project_id = self._project_id(task_id, create=False)
        if project_id is None:
            return None
        for experiment in reversed(self.db.get_experiments_by_project(project_id)):
            inputs = experiment.get("inputs")
            if isinstance(inputs, Mapping) and isinstance(inputs.get("context"), Mapping):
                return experiment
        return None

    def _require_permission(
        self,
        actor: str,
        capability: str,
        task_id: str,
        operation: str,
        permission: Mapping[str, Any] | None,
    ) -> None:
        # Reuse the project's permission manager instead of a second policy.
        from matlab_os.orchestration.permissions import PermissionManager

        if permission is not None:
            manager = PermissionManager(**dict(permission))
        else:
            manager = PermissionManager(default_allow=True)
        if not manager.is_allowed(actor, capability, task_id, operation):
            raise PermissionDeniedError(
                f"actor {actor!r} is not allowed to {operation} memory for {task_id!r}."
            )
