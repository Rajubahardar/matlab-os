"""Multi-agent coordination for the Phase 12 ecosystem.

Reuses the Phase 9 :class:`matlab_os.phase9.collaboration.coordinator
.CollaborationCoordinator` and adds dependency-graph validation, explicit
handoff records, provenance and result verification before a downstream agent
treats a value as fact.

No second collaboration engine is introduced: aggregation and conflict
detection are delegated to the Phase 9 coordinator.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from matlab_os.phase9.collaboration.coordinator import (
    CollaborationCoordinator,
)

from matlab_os.integration.agent_ecosystem.contract import validate_result


class CoordinationError(ValueError):
    """A coordination operation is not valid."""


class CircularDependencyError(CoordinationError):
    """Task dependency graph contains a cycle."""


class DependencyNotCompleteError(CoordinationError):
    """A required upstream task has not completed."""


class ConflictDetectedError(CoordinationError):
    """Upstream results disagree irreconcilably."""


class Coordination:
    """Coordinated multi-agent execution with provenance and conflict detection."""

    def __init__(self, coordinator: CollaborationCoordinator) -> None:
        self.coordinator = coordinator
        self._lock = threading.RLock()
        self._deps: dict[str, set[str]] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._handoffs: dict[str, dict[str, Any]] = {}
        self._failures: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # Public API                                                        #
    # ------------------------------------------------------------------ #

    def declare_dependency(self, task_id: str, dependency: str) -> dict[str, Any]:
        """Declare that ``task_id`` consumes ``dependency``'s result."""
        task_id = self._require_text(task_id, "task_id")
        dependency = self._require_text(dependency, "dependency")
        if task_id == dependency:
            raise CircularDependencyError(f"task {task_id!r} cannot depend on itself.")
        with self._lock:
            self._deps.setdefault(task_id, set()).add(dependency)
            graph = {node: set(deps) for node, deps in self._deps.items()}
            cycles = self._find_cycles(graph)
            if cycles:
                self._deps[task_id].discard(dependency)
                raise CircularDependencyError(f"dependency cycle detected: {cycles}")
            return {"task_id": task_id, "dependency": dependency, "dependencies": sorted(self._deps[task_id])}

    def validate_dependency_graph(self, task_id: str, dependencies: Mapping[str, Any]) -> dict[str, Any]:
        task_id = self._require_text(task_id, "task_id")
        if not isinstance(dependencies, Mapping):
            raise CoordinationError("dependencies must be a mapping.")
        graph = {node: set(deps) for node, deps in dependencies.items()}
        graph.setdefault(task_id, set())
        cycles = self._find_cycles(graph)
        if cycles:
            raise CircularDependencyError(f"dependency cycle detected: {cycles}")
        return {"task_id": task_id, "dependencies": {k: sorted(v) for k, v in graph.items()}, "cycles": []}

    def register_result(self, source_task: str, result: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and record an upstream result before it may be handed off."""
        source_task = self._require_text(source_task, "source_task")
        verified = validate_result(result)
        if verified.is_failure:
            raise CoordinationError(
                f"upstream task {source_task!r} did not complete: {verified.status}."
            )
        recorded_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            payload = verified.to_dict()
            self._results[source_task] = {"result": payload, "recorded_at": recorded_at}
            self._handoffs[source_task] = {"result": payload, "recorded_at": recorded_at}
            return {
                "task": source_task,
                "status": verified.status,
                "provenance": list(verified.provenance),
                "recorded_at": recorded_at,
            }

    def record_failure(self, source_task: str, result: Mapping[str, Any]) -> dict[str, Any]:
        """Record a failed result so its provenance survives the failure.

        A failure is never exposed as a handoff: downstream agents must not
        treat it as fact.  The record exists so the failure, its status and its
        provenance remain auditable after the run has ended.
        """

        source_task = self._require_text(source_task, "source_task")
        verified = validate_result(result)
        if not verified.is_failure:
            raise CoordinationError(
                f"result for {source_task!r} is not a failure: {verified.status}."
            )
        recorded_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._failures[source_task] = {
                "result": verified.to_dict(),
                "recorded_at": recorded_at,
            }
            return {
                "task": source_task,
                "status": verified.status,
                "provenance": list(verified.provenance),
                "recorded_at": recorded_at,
            }

    def failure(self, task_id: str) -> dict[str, Any] | None:
        """Return the recorded failure for ``task_id``, if there is one."""

        task_id = self._require_text(task_id, "task_id")
        with self._lock:
            entry = self._failures.get(task_id)
            return dict(entry) if entry is not None else None

    def handoff(self, task_id: str, dependency: str) -> dict[str, Any]:
        task_id = self._require_text(task_id, "task_id")
        dependency = self._require_text(dependency, "dependency")
        with self._lock:
            if dependency not in self._results:
                raise DependencyNotCompleteError(f"upstream task {dependency!r} has no recorded result.")
            self._deps.setdefault(task_id, set()).add(dependency)
            return {
                "task_id": task_id,
                "dependency": dependency,
                "value": self._results[dependency]["result"],
                "recorded_at": self._handoffs[dependency]["recorded_at"],
            }

    def aggregate(self, task_id: str, results: Mapping[str, Any]) -> Any:
        return self.coordinator.aggregate(task_id, results)

    def conflicts(self, results: Mapping[str, Any]) -> list[dict[str, Any]]:
        return self.coordinator.detect_conflicts(results)

    def provenance(self, task_id: str) -> dict[str, Any]:
        task_id = self._require_text(task_id, "task_id")
        with self._lock:
            declared = self._deps.get(task_id, set())
            # Without an explicit declaration, every recorded result is an
            # upstream contribution to this task (a task never contributes to
            # its own provenance).
            upstream = declared or (set(self._results) - {task_id})
            handoff = self._handoffs.get(task_id)
            return {
                "task_id": task_id,
                "downstream_agents": sorted(
                    agent for agent, deps in self._deps.items() if task_id in deps and agent != task_id
                ),
                "upstream_agents": sorted(upstream),
                "recorded_at": handoff["recorded_at"] if handoff else None,
                "result": handoff["result"] if handoff else None,
            }

    # ------------------------------------------------------------------ #
    # Internals                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _require_text(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string.")
        return value

    def _find_cycles(self, graph: Mapping[str, set[str]]) -> list[list[str]]:
        visited: set[str] = set()
        stack: list[str] = []
        cycles: list[list[str]] = []

        def dfs(node: str) -> None:
            visited.add(node)
            stack.append(node)
            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in stack:
                    index = stack.index(neighbor)
                    cycles.append(stack[index:] + [neighbor])
            stack.pop()

        for node in graph:
            if node not in visited:
                dfs(node)
        return cycles
