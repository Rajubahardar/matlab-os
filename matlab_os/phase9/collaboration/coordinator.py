"""Controlled multi-agent collaboration for Phase 9 (staging only).

The coordinator is the single authority over delegation.  It enforces every
documented budget *before* any state change:

* maximum delegation depth,
* a cycle-free parent -> child graph per task,
* maximum total delegations, maximum distinct agents per task and maximum
  protocol messages,
* a per-delegation timeout measured with an injectable clock.

Every state transition is written to the injected audit log.  Aggregation never
silently chooses between disagreeing participants: conflicts are reported and
the aggregate status becomes ``CONFLICT``.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..audit import NullAuditLog
from ..config import CollaborationConfig
from ..errors import (
    CollaborationError,
    CollaborationTimeoutError,
    DelegationCycleError,
    DelegationDepthExceededError,
    DelegationLimitError,
)
from ..security.redaction import redact, scrub_text
from .protocol import AgentMessage, MessageKind

# -- delegation states ------------------------------------------------------ #
DELEGATION_PENDING = "pending"
DELEGATION_ACCEPTED = "accepted"
DELEGATION_COMPLETED = "completed"
DELEGATION_FAILED = "failed"
DELEGATION_TIMEOUT = "timeout"
DELEGATION_CANCELLED = "cancelled"

DELEGATION_STATUSES: frozenset[str] = frozenset(
    {
        DELEGATION_PENDING,
        DELEGATION_ACCEPTED,
        DELEGATION_COMPLETED,
        DELEGATION_FAILED,
        DELEGATION_TIMEOUT,
        DELEGATION_CANCELLED,
    }
)

# -- aggregation statuses --------------------------------------------------- #
AGGREGATION_COMPLETED = "COMPLETED"
AGGREGATION_CONFLICT = "CONFLICT"
AGGREGATION_PARTIAL = "PARTIAL"
AGGREGATION_FAILED = "FAILED"

#: Top-level result keys that describe the participant outcome rather than
#: result data.  They are excluded from conflict comparison so that a completed
#: participant and a failed participant cannot be reported as conflicting on
#: their outcome marker alone.
_OUTCOME_KEYS: frozenset[str] = frozenset({"status", "failed"})

#: Upper bound on the number of conflict keys copied into one audit record.
_AUDIT_CONFLICT_KEYS = 20


@dataclass
class DelegationRecord:
    """One parent -> child delegation and its lifecycle."""

    delegation_id: str
    parent: str
    child: str
    task_id: str
    depth: int
    created_at: float
    timeout_seconds: float
    status: str = DELEGATION_PENDING
    result: Any = None
    error: str | None = None
    accepted_at: float | None = None
    finished_at: float | None = None

    def elapsed(self, now: float) -> float:
        """Return seconds elapsed since creation (never negative)."""
        return max(0.0, now - self.created_at)

    def is_timed_out(self, now: float) -> bool:
        """Return True when the delegation exceeded its timeout."""
        return self.timeout_seconds > 0 and self.elapsed(now) > self.timeout_seconds

    def to_dict(self) -> dict[str, Any]:
        """Return a redacted, JSON-safe view of the record."""
        return {
            "delegation_id": self.delegation_id,
            "parent": self.parent,
            "child": self.child,
            "task_id": self.task_id,
            "depth": self.depth,
            "created_at": self.created_at,
            "timeout_seconds": self.timeout_seconds,
            "status": self.status,
            "result": redact(self.result),
            "error": self.error,
            "accepted_at": self.accepted_at,
            "finished_at": self.finished_at,
        }


@dataclass
class AggregationResult:
    """Outcome of aggregating participant results for one task."""

    task_id: str
    results: dict[str, Any]
    conflicts: list[dict[str, Any]]
    status: str
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "results": redact(self.results),
            "conflicts": [dict(conflict) for conflict in self.conflicts],
            "status": self.status,
            "explanation": self.explanation,
        }


class CollaborationCoordinator:
    """Depth, cycle, width, total, message and timeout enforcement.

    Args:
        config: validated :class:`CollaborationConfig`.
        audit: audit sink exposing ``record(action, actor, outcome, details)``;
            defaults to an in-memory :class:`NullAuditLog`.
        directory: optional :class:`AgentDirectory` reference for runtime
            wiring.  It is not consulted for enforcement so that the
            coordinator remains usable in isolation.
        clock: injectable time source (defaults to ``time.time``) so timeouts
            are deterministic in tests.
    """

    def __init__(
        self,
        config: CollaborationConfig,
        *,
        audit: Any | None = None,
        directory: Any | None = None,
        clock: Any = time.time,
    ) -> None:
        if config is None:
            # A coordinator without an explicit budget configuration uses the
            # documented Phase 9 defaults rather than failing at construction.
            from matlab_os.phase9.config import CollaborationConfig

            config = CollaborationConfig()
        required = (
            "max_delegation_depth",
            "max_agents_per_task",
            "max_total_delegations",
            "max_messages",
            "max_payload_bytes",
            "message_timeout_seconds",
            "detect_conflicts",
        )
        missing = [name for name in required if not hasattr(config, name)]
        if missing:
            raise ValueError(
                "config is missing required collaboration attribute(s): "
                + ", ".join(sorted(missing))
            )
        validate = getattr(config, "validate", None)
        if callable(validate):
            validate()
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable.")
        audit_sink = audit if audit is not None else NullAuditLog()
        if not callable(getattr(audit_sink, "record", None)):
            raise ValueError("audit must expose a record() method.")

        self._config = config
        self._audit = audit_sink
        self._directory = directory
        self._clock = clock if clock is not None else time.time
        self._lock = threading.RLock()

        self._delegations: dict[str, DelegationRecord] = {}
        self._children: dict[str, set[str]] = {}
        self._task_delegations: dict[str, list[str]] = {}
        self._task_agents: dict[str, set[str]] = {}
        self._messages_by_task: dict[str, int] = {}
        self._message_count = 0
        self._timeouts = 0
        self._conflicts_detected = 0

    # -- accessors ---------------------------------------------------------- #
    @property
    def config(self) -> CollaborationConfig:
        """The validated configuration this coordinator obeys."""
        return self._config

    # -- delegation lifecycle ----------------------------------------------- #
    def delegate(
        self,
        *,
        parent: str,
        child: str,
        task_id: str,
        payload: Mapping[str, Any],
        depth: int = 0,
        timeout_seconds: float | None = None,
    ) -> DelegationRecord:
        """Create one delegation after enforcing every budget.

        Raises:
            DelegationDepthExceededError: ``depth`` exceeds the configured
                maximum.
            DelegationCycleError: the child is already in this task's chain.
            DelegationLimitError: a total/width/message budget is exhausted.
            CollaborationError: the delegation message payload is oversized or
                malformed.
        """
        parent_id = _require_identifier(parent, "parent")
        child_id = _require_identifier(child, "child")
        task = _require_identifier(task_id, "task_id")
        body = _require_payload(payload)
        delegation_depth = _require_depth(depth)
        timeout = self._resolve_timeout(timeout_seconds)

        with self._lock:
            if delegation_depth > self._config.max_delegation_depth:
                self._audit_event(
                    "collab.depth",
                    parent_id,
                    "denied",
                    task_id=task,
                    parent=parent_id,
                    child=child_id,
                    depth=delegation_depth,
                    max_delegation_depth=self._config.max_delegation_depth,
                )
                raise DelegationDepthExceededError(
                    "Delegation depth "
                    f"{delegation_depth} exceeds the configured maximum "
                    f"{self._config.max_delegation_depth}.",
                    depth=delegation_depth,
                    max_delegation_depth=self._config.max_delegation_depth,
                )

            chain = self._chain_for(task, parent_id)
            if child_id in chain:
                self._audit_event(
                    "collab.cycle",
                    parent_id,
                    "denied",
                    task_id=task,
                    parent=parent_id,
                    child=child_id,
                    chain=sorted(chain),
                )
                raise DelegationCycleError(
                    f"Delegation {parent_id!r} -> {child_id!r} would form a "
                    f"cycle in task {task!r}; chain={sorted(chain)}.",
                    parent=parent_id,
                    child=child_id,
                    task_id=task,
                )

            if len(self._delegations) >= self._config.max_total_delegations:
                self._audit_event(
                    "collab.limit",
                    parent_id,
                    "denied",
                    limit="max_total_delegations",
                    value=self._config.max_total_delegations,
                    task_id=task,
                )
                raise DelegationLimitError(
                    "Maximum total delegations reached "
                    f"({self._config.max_total_delegations}).",
                    limit="max_total_delegations",
                    max_total_delegations=self._config.max_total_delegations,
                )

            participants = set(self._task_agents.get(task, ()))
            participants.update((parent_id, child_id))
            if len(participants) > self._config.max_agents_per_task:
                self._audit_event(
                    "collab.limit",
                    parent_id,
                    "denied",
                    limit="max_agents_per_task",
                    value=self._config.max_agents_per_task,
                    task_id=task,
                    agents=sorted(participants),
                )
                raise DelegationLimitError(
                    "Maximum distinct agents per task would be exceeded "
                    f"({len(participants)} > "
                    f"{self._config.max_agents_per_task}) for task {task!r}.",
                    limit="max_agents_per_task",
                    max_agents_per_task=self._config.max_agents_per_task,
                    task_id=task,
                    agents=sorted(participants),
                )

            delegation_id = str(uuid.uuid4())
            now = self._clock()
            self._emit_message(
                MessageKind.DELEGATE,
                sender=parent_id,
                recipient=child_id,
                task_id=task,
                correlation_id=delegation_id,
                depth=delegation_depth,
                payload=body,
            )
            record = DelegationRecord(
                delegation_id=delegation_id,
                parent=parent_id,
                child=child_id,
                task_id=task,
                depth=delegation_depth,
                created_at=now,
                timeout_seconds=timeout,
                status=DELEGATION_PENDING,
            )
            self._delegations[delegation_id] = record
            self._children.setdefault(parent_id, set()).add(child_id)
            self._task_delegations.setdefault(task, []).append(delegation_id)
            self._task_agents.setdefault(task, set()).update(
                (parent_id, child_id)
            )
            self._audit_event(
                "collab.delegate",
                parent_id,
                "success",
                delegation_id=delegation_id,
                task_id=task,
                parent=parent_id,
                child=child_id,
                depth=delegation_depth,
                timeout_seconds=timeout,
            )
            return record

    def accept(self, delegation_id: str) -> DelegationRecord:
        """Mark a pending delegation as accepted by the child.

        Raises:
            CollaborationTimeoutError: the delegation deadline has passed; the
                record is marked ``timeout`` before the error is raised.
            CollaborationError: unknown id or invalid state transition.
        """
        with self._lock:
            record = self._require_delegation(delegation_id)
            now = self._clock()
            if record.status != DELEGATION_PENDING:
                self._invalid_transition(record, "accept")
                raise CollaborationError(
                    f"Delegation {record.delegation_id!r} cannot be accepted "
                    f"in state {record.status!r}.",
                    delegation_id=record.delegation_id,
                    status=record.status,
                )
            if record.is_timed_out(now):
                raise self._mark_timeout(record, now, "accept")
            self._emit_message(
                MessageKind.ACCEPT,
                sender=record.child,
                recipient=record.parent,
                task_id=record.task_id,
                correlation_id=record.delegation_id,
                depth=record.depth,
                payload={
                    "delegation_id": record.delegation_id,
                    "status": DELEGATION_ACCEPTED,
                },
            )
            record.status = DELEGATION_ACCEPTED
            record.accepted_at = now
            self._audit_event(
                "collab.accept",
                record.child,
                "success",
                delegation_id=record.delegation_id,
                task_id=record.task_id,
                parent=record.parent,
                child=record.child,
            )
            return record

    def complete(self, delegation_id: str, result: Any) -> DelegationRecord:
        """Record a successful delegation result.

        Completion is accepted from ``pending`` (the explicit accept message is
        optional) or ``accepted``.

        Raises:
            CollaborationTimeoutError: the delegation deadline has passed.
            CollaborationError: unknown id or invalid state transition.
        """
        with self._lock:
            record = self._require_delegation(delegation_id)
            now = self._clock()
            if record.status not in (DELEGATION_PENDING, DELEGATION_ACCEPTED):
                self._invalid_transition(record, "complete")
                raise CollaborationError(
                    f"Delegation {record.delegation_id!r} cannot be completed "
                    f"in state {record.status!r}.",
                    delegation_id=record.delegation_id,
                    status=record.status,
                )
            if record.is_timed_out(now):
                raise self._mark_timeout(record, now, "complete")
            self._emit_message(
                MessageKind.RESULT,
                sender=record.child,
                recipient=record.parent,
                task_id=record.task_id,
                correlation_id=record.delegation_id,
                depth=record.depth,
                payload={
                    "delegation_id": record.delegation_id,
                    "status": DELEGATION_COMPLETED,
                    "result_type": type(result).__name__,
                },
            )
            record.result = result
            record.status = DELEGATION_COMPLETED
            record.finished_at = now
            self._audit_event(
                "collab.complete",
                record.child,
                "success",
                delegation_id=record.delegation_id,
                task_id=record.task_id,
                parent=record.parent,
                child=record.child,
            )
            return record

    def fail(
        self, delegation_id: str, error: BaseException | str
    ) -> DelegationRecord:
        """Record a failed delegation (secret-free error text is stored).

        Raises:
            CollaborationError: unknown id or invalid state transition.
        """
        with self._lock:
            record = self._require_delegation(delegation_id)
            if record.status not in (DELEGATION_PENDING, DELEGATION_ACCEPTED):
                self._invalid_transition(record, "fail")
                raise CollaborationError(
                    f"Delegation {record.delegation_id!r} cannot be marked "
                    f"failed in state {record.status!r}.",
                    delegation_id=record.delegation_id,
                    status=record.status,
                )
            description = self._describe_failure(error)
            self._emit_message(
                MessageKind.ERROR,
                sender=record.child,
                recipient=record.parent,
                task_id=record.task_id,
                correlation_id=record.delegation_id,
                depth=record.depth,
                payload={
                    "delegation_id": record.delegation_id,
                    "status": DELEGATION_FAILED,
                    "error": description[:500],
                },
            )
            record.error = description
            record.status = DELEGATION_FAILED
            record.finished_at = self._clock()
            self._audit_event(
                "collab.fail",
                record.child,
                "success",
                delegation_id=record.delegation_id,
                task_id=record.task_id,
                parent=record.parent,
                child=record.child,
            )
            return record

    # -- aggregation -------------------------------------------------------- #
    def aggregate(
        self, task_id: str, results: Mapping[str, Any]
    ) -> AggregationResult:
        """Aggregate participant results without choosing between conflicts.

        Status precedence: ``FAILED`` when no participant (or every
        participant) failed, ``CONFLICT`` when shared keys disagree,
        ``PARTIAL`` when only some participants failed, else ``COMPLETED``.
        """
        task = _require_identifier(task_id, "task_id")
        results_map = _require_results_mapping(results)
        conflicts = self.detect_conflicts(results_map)
        failures = sorted(
            agent
            for agent, value in results_map.items()
            if _is_failure(value)
        )
        with self._lock:
            if conflicts:
                self._conflicts_detected += 1
                self._audit_event(
                    "collab.conflict",
                    "coordinator",
                    "conflict",
                    task_id=task,
                    conflict_count=len(conflicts),
                    keys=[
                        conflict["key"]
                        for conflict in conflicts[:_AUDIT_CONFLICT_KEYS]
                    ],
                )

            if not results_map:
                status = AGGREGATION_FAILED
                explanation = (
                    "No participant results were supplied; "
                    "nothing completed."
                )
            elif len(failures) == len(results_map):
                status = AGGREGATION_FAILED
                explanation = (
                    f"All {len(results_map)} participant(s) failed: "
                    + ", ".join(failures)
                    + "."
                )
            elif conflicts:
                status = AGGREGATION_CONFLICT
                explanation = (
                    f"{len(conflicts)} conflicting value(s) across "
                    "participants; no value is chosen. Conflicting keys: "
                    + ", ".join(
                        conflict["key"] for conflict in conflicts
                    )
                    + "."
                )
            elif failures:
                status = AGGREGATION_PARTIAL
                explanation = (
                    f"{len(results_map) - len(failures)} participant(s) "
                    f"completed and {len(failures)} failed: "
                    + ", ".join(failures)
                    + "."
                )
            else:
                status = AGGREGATION_COMPLETED
                explanation = (
                    f"All {len(results_map)} participant(s) completed with "
                    "no conflicting values."
                )

            self._audit_event(
                "collab.aggregate",
                "coordinator",
                status.lower(),
                task_id=task,
                status=status,
                participants=len(results_map),
                conflict_count=len(conflicts),
            )
            return AggregationResult(
                task_id=task,
                results=dict(results_map),
                conflicts=conflicts,
                status=status,
                explanation=explanation,
            )

    def detect_conflicts(
        self, results: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """Report disagreeing nested result values as conflict dictionaries.

        Each entry contains exactly ``"key"`` (a dotted path), ``"values"``
        (the distinct observed values) and ``"agents"`` (the sorted participant
        ids that supplied a value for that key).  Only keys shared by at least
        two participants are compared; absent keys are not conflicts.
        """
        results_map = _require_results_mapping(results)
        if not self._config.detect_conflicts:
            return []
        entries = sorted(results_map.items(), key=lambda item: item[0])
        return _conflicts_in(entries, "")

    # -- inventory ---------------------------------------------------------- #
    def inventory(self) -> dict[str, Any]:
        """Return a deterministic, redacted inventory of collaboration state."""
        with self._lock:
            records = sorted(
                self._delegations.values(),
                key=lambda record: (
                    record.task_id,
                    record.depth,
                    record.delegation_id,
                ),
            )
            by_status: dict[str, int] = {}
            for record in records:
                by_status[record.status] = by_status.get(record.status, 0) + 1
            return {
                "delegations": len(records),
                "messages": self._message_count,
                "by_status": dict(sorted(by_status.items())),
                "agents_per_task": {
                    task: sorted(agents)
                    for task, agents in sorted(self._task_agents.items())
                },
                "messages_per_task": {
                    task: count
                    for task, count in sorted(self._messages_by_task.items())
                },
                "children": {
                    parent: sorted(children)
                    for parent, children in sorted(self._children.items())
                },
                "graph": [
                    {
                        "delegation_id": record.delegation_id,
                        "task_id": record.task_id,
                        "parent": record.parent,
                        "child": record.child,
                        "depth": record.depth,
                        "status": record.status,
                    }
                    for record in records
                ],
                "records": [record.to_dict() for record in records],
                "timeouts": self._timeouts,
                "conflicts_detected": self._conflicts_detected,
                "limits": {
                    "max_delegation_depth": self._config.max_delegation_depth,
                    "max_agents_per_task": self._config.max_agents_per_task,
                    "max_total_delegations": (
                        self._config.max_total_delegations
                    ),
                    "max_messages": self._config.max_messages,
                    "max_payload_bytes": self._config.max_payload_bytes,
                    "message_timeout_seconds": (
                        self._config.message_timeout_seconds
                    ),
                },
            }

    # -- internals ---------------------------------------------------------- #
    def _audit_event(
        self, action: str, actor: str, outcome: str, **details: Any
    ) -> None:
        """Write one audit record; credentials never reach ``details``."""
        self._audit.record(
            action=action,
            actor=actor,
            outcome=outcome,
            details=details,
        )

    def _emit_message(
        self,
        kind: MessageKind,
        *,
        sender: str,
        recipient: str,
        task_id: str,
        correlation_id: str,
        depth: int,
        payload: dict[str, Any],
    ) -> AgentMessage:
        """Validate and account for one protocol message.

        Raises:
            DelegationLimitError: the message budget is exhausted.
            CollaborationError: the payload exceeds the configured byte budget.
        """
        if self._message_count >= self._config.max_messages:
            self._audit_event(
                "collab.limit",
                sender,
                "denied",
                limit="max_messages",
                value=self._config.max_messages,
                task_id=task_id,
            )
            raise DelegationLimitError(
                "Maximum collaboration message budget reached "
                f"({self._config.max_messages}).",
                limit="max_messages",
                max_messages=self._config.max_messages,
                task_id=task_id,
            )
        message = AgentMessage(
            message_id=str(uuid.uuid4()),
            kind=kind,
            sender=sender,
            recipient=recipient,
            task_id=task_id,
            correlation_id=correlation_id,
            depth=depth,
            payload=payload,
            created_at=self._clock(),
        )
        message.validate(self._config.max_payload_bytes)
        self._message_count += 1
        self._messages_by_task[task_id] = (
            self._messages_by_task.get(task_id, 0) + 1
        )
        return message

    def _resolve_timeout(self, timeout_seconds: float | None) -> float:
        if timeout_seconds is None:
            return float(self._config.message_timeout_seconds)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError(
                "timeout_seconds must be a positive number or None."
            )
        return float(timeout_seconds)

    def _require_delegation(self, delegation_id: Any) -> DelegationRecord:
        if not isinstance(delegation_id, str) or not delegation_id.strip():
            self._audit_event(
                "collab.error",
                "coordinator",
                "failure",
                reason="unknown_delegation",
                delegation_id=repr(delegation_id),
            )
            raise CollaborationError(
                "Unknown delegation id.", delegation_id=repr(delegation_id)
            )
        record = self._delegations.get(delegation_id)
        if record is None:
            self._audit_event(
                "collab.error",
                "coordinator",
                "failure",
                reason="unknown_delegation",
                delegation_id=delegation_id,
            )
            raise CollaborationError(
                f"Unknown delegation id: {delegation_id!r}",
                delegation_id=delegation_id,
            )
        return record

    def _invalid_transition(
        self, record: DelegationRecord, stage: str
    ) -> None:
        self._audit_event(
            "collab.error",
            record.child,
            "failure",
            reason="invalid_transition",
            stage=stage,
            delegation_id=record.delegation_id,
            task_id=record.task_id,
            status=record.status,
        )

    def _mark_timeout(
        self, record: DelegationRecord, now: float, stage: str
    ) -> CollaborationTimeoutError:
        """Record a timeout on *record* and build the matching error."""
        elapsed = record.elapsed(now)
        record.status = DELEGATION_TIMEOUT
        record.finished_at = now
        self._timeouts += 1
        self._audit_event(
            "collab.timeout",
            record.child,
            "failure",
            delegation_id=record.delegation_id,
            task_id=record.task_id,
            parent=record.parent,
            child=record.child,
            stage=stage,
            elapsed_seconds=round(elapsed, 6),
            timeout_seconds=record.timeout_seconds,
        )
        return CollaborationTimeoutError(
            f"Delegation {record.delegation_id!r} exceeded its "
            f"{record.timeout_seconds}s timeout before {stage}.",
            delegation_id=record.delegation_id,
            elapsed_seconds=round(elapsed, 6),
            timeout_seconds=record.timeout_seconds,
        )

    def _chain_for(self, task_id: str, parent: str) -> set[str]:
        """Return the ancestor set (including *parent*) for one task chain."""
        parents_of: dict[str, set[str]] = {}
        for delegation_id in self._task_delegations.get(task_id, ()):
            record = self._delegations.get(delegation_id)
            if record is None:
                continue
            parents_of.setdefault(record.child, set()).add(record.parent)
        chain: set[str] = {parent}
        stack: list[str] = [parent]
        while stack:
            node = stack.pop()
            for ancestor in parents_of.get(node, ()):
                if ancestor not in chain:
                    chain.add(ancestor)
                    stack.append(ancestor)
        return chain

    @staticmethod
    def _describe_failure(error: BaseException | str) -> str:
        """Render a secret-free, bounded failure description."""
        if isinstance(error, BaseException):
            text = f"{type(error).__name__}: {scrub_text(str(error))}"
        else:
            text = scrub_text(str(error))
        text = text.strip()
        if not text:
            text = "unspecified failure"
        return text[:2000]


# --------------------------------------------------------------------------- #
# Module-level helpers
# --------------------------------------------------------------------------- #
def _require_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value


def _require_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping.")
    return dict(payload)


def _require_depth(depth: Any) -> int:
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
        raise ValueError("depth must be an integer >= 0.")
    return depth


def _require_results_mapping(results: Any) -> dict[str, Any]:
    if not isinstance(results, Mapping):
        raise ValueError("results must be a mapping of agent_id to result.")
    normalized: dict[str, Any] = {}
    for agent, value in results.items():
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError(
                "results keys must be non-empty agent ids."
            )
        normalized[agent] = value
    return normalized


def _canonical_value(value: Any) -> str:
    """Best-effort deterministic rendering used only for equality fallback."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return repr(value)


def _values_equal(left: Any, right: Any) -> bool:
    """Return True when two arbitrary result values are equal."""
    try:
        return bool(left == right)
    except Exception:  # pragma: no cover - defensive
        return _canonical_value(left) == _canonical_value(right)


def _is_failure(value: Any) -> bool:
    """Return True when a participant result represents a failure."""
    if isinstance(value, BaseException):
        return True
    if isinstance(value, Mapping):
        failed_flag = value.get("failed")
        if isinstance(failed_flag, bool) and failed_flag:
            return True
        status = value.get("status")
        if isinstance(status, str) and status.strip().upper() in {
            "FAILED",
            "FAILURE",
            "ERROR",
            "TIMEOUT",
            "CANCELLED",
        }:
            return True
    return False


def _conflicts_in(
    entries: list[tuple[str, Any]], prefix: str
) -> list[dict[str, Any]]:
    """Recursively compare nested mappings on their shared keys."""
    mappings = [
        (agent, value) for agent, value in entries if isinstance(value, Mapping)
    ]
    if len(mappings) < 2:
        return []

    keys = sorted(
        {key for _, value in mappings for key in value}, key=repr
    )
    conflicts: list[dict[str, Any]] = []
    for key in keys:
        if not prefix and key in _OUTCOME_KEYS:
            continue
        key_name = f"{prefix}.{key}" if prefix else str(key)
        present = [
            (agent, value[key])
            for agent, value in mappings
            if key in value
        ]
        if len(present) < 2:
            # Only shared keys are compared; presence alone is not a conflict.
            continue
        if all(isinstance(value, Mapping) for _, value in present):
            conflicts.extend(_conflicts_in(present, key_name))
            continue

        groups: list[tuple[Any, list[str]]] = []
        for agent, value in present:
            for representative, agents in groups:
                if _values_equal(representative, value):
                    agents.append(agent)
                    break
            else:
                groups.append((value, [agent]))
        if len(groups) > 1:
            conflicts.append(
                {
                    "key": key_name,
                    "values": [
                        representative for representative, _ in groups
                    ],
                    "agents": sorted(
                        agent for _, agents in groups for agent in agents
                    ),
                }
            )
    return conflicts


__all__ = [
    "DELEGATION_PENDING",
    "DELEGATION_ACCEPTED",
    "DELEGATION_COMPLETED",
    "DELEGATION_FAILED",
    "DELEGATION_TIMEOUT",
    "DELEGATION_CANCELLED",
    "DELEGATION_STATUSES",
    "AGGREGATION_COMPLETED",
    "AGGREGATION_CONFLICT",
    "AGGREGATION_PARTIAL",
    "AGGREGATION_FAILED",
    "DelegationRecord",
    "AggregationResult",
    "CollaborationCoordinator",
]
