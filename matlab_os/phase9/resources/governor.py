"""Resource admission control for Phase 9 (staging only).

The governor is the single entry point through which Phase 9 work obtains a
resource slot.  It composes two reused pieces read-only:

* :class:`matlab_os.phase9.resources.monitor.ResourceMonitor` supplies
  stdlib CPU/RAM telemetry as a
  :class:`matlab_os.intelligence.resource_scheduler.ResourceSnapshot`;
* :class:`matlab_os.intelligence.resource_scheduler.ResourceScheduler` performs
  the admission arithmetic (``submit`` -> ``mark_approved`` ->
  ``dispatch_next`` -> ``complete``).

Behaviour
---------
* ``max_concurrent_agents`` is enforced before the scheduler is touched.
* A missing, incomplete or stale telemetry reading is **never** silently
  treated as healthy: when ``fail_closed_without_telemetry`` is enabled the
  admission is refused; otherwise it is admitted in an explicitly recorded
  degraded mode that bypasses the resource scheduler because no trustworthy
  snapshot exists.
* ``release`` is idempotent; an unknown or already released task cannot corrupt
  the slot accounting.
* Subprocess slots are a separate, thread-safe counter ceiling.
* ``status`` never raises: degraded introspection is reported as data.
* Every decision that could mutate state is mirrored to the audit log; an
  audit failure is counted in ``status`` instead of turning a completed
  admission into an inconsistent exception.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Optional

from matlab_os.intelligence.resource_scheduler import (
    DuplicateTaskError,
    ResourceRequirements,
    ResourceScheduler,
    ResourceSnapshot,
    SchedulerError,
    TaskState,
    UnknownTaskError,
)

from ..config import ResourceConfig
from ..errors import (
    ConcurrencyLimitError,
    ResourceExhaustedError,
    TelemetryUnavailableError,
)
from .monitor import ResourceMonitor

#: Admission outcome codes (machine readable, stable).
ADMITTED = "admitted"
DEGRADED = "degraded"
CONCURRENCY_LIMIT = "concurrency_limit"
TELEMETRY_UNAVAILABLE = "telemetry_unavailable"
RESOURCE_EXHAUSTED = "resource_exhausted"


@dataclass(frozen=True)
class Admission:
    """Result of one admission decision."""

    admitted: bool
    reason: str
    task_id: str
    slot: Optional[int] = None
    code: str = ADMITTED
    degraded: bool = False
    telemetry_available: bool = True

    def describe(self) -> dict[str, Any]:
        """Return a serialisable, secret-free description."""
        return {
            "task_id": self.task_id,
            "admitted": self.admitted,
            "reason": self.reason,
            "slot": self.slot,
            "code": self.code,
            "degraded": self.degraded,
            "telemetry_available": self.telemetry_available,
        }


class _SubmissionRefused(Exception):
    """Internal signal: the scheduler refused to accept the task."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _validate_config(config: ResourceConfig) -> None:
    """Validate only the fields the governor depends on.

    Invalid configuration is a programmer error, hence ``ValueError``.
    """
    for name in (
        "max_concurrent_agents",
        "max_concurrent_subprocesses",
    ):
        value = getattr(config, name, None)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be an integer >= 1.")
    if not 0 < float(config.max_cpu_load_percent) <= 100.0:
        raise ValueError("max_cpu_load_percent must be within (0, 100].")
    if float(config.min_free_ram_gb) < 0.0:
        raise ValueError("min_free_ram_gb cannot be negative.")
    if float(config.max_process_ram_gb) <= 0.0:
        raise ValueError("max_process_ram_gb must be positive.")
    if float(config.telemetry_sample_seconds) <= 0.0:
        raise ValueError("telemetry_sample_seconds must be positive.")
    if float(config.telemetry_max_age_seconds) <= 0.0:
        raise ValueError("telemetry_max_age_seconds must be positive.")


class ResourceGovernor:
    """Thread-safe admission control, concurrency and subprocess ceilings."""

    def __init__(
        self,
        config: ResourceConfig,
        *,
        monitor: Any = None,
        scheduler: Any = None,
        audit: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(config, ResourceConfig):
            raise TypeError("config must be a ResourceConfig instance.")
        _validate_config(config)
        if not callable(clock):
            raise TypeError("clock must be callable.")

        self.config = config
        self._clock = clock
        self._max_agents = int(config.max_concurrent_agents)
        self._max_subprocesses = int(config.max_concurrent_subprocesses)

        if monitor is None:
            monitor = ResourceMonitor(
                sample_seconds=float(config.telemetry_sample_seconds),
                max_age_seconds=float(config.telemetry_max_age_seconds),
                clock=clock,
            )
        if not callable(getattr(monitor, "sample", None)):
            raise TypeError("monitor must expose a sample() method.")
        self._monitor = monitor

        if scheduler is None:
            scheduler = ResourceScheduler(
                max_concurrent=self._max_agents,
                max_cpu_load_percent=float(config.max_cpu_load_percent),
                minimum_free_ram_gb=float(config.min_free_ram_gb),
                thermal_limit_c=config.thermal_limit_c,
            )
        if not callable(getattr(scheduler, "update_resources", None)):
            raise TypeError(
                "scheduler must be a ResourceScheduler-compatible object."
            )
        self._scheduler = scheduler

        self._audit = audit
        self._lock = threading.RLock()

        self._admitted: dict[str, Admission] = {}
        self._requirements: dict[str, ResourceRequirements] = {}
        self._subprocess_in_use = 0

        self._admission_count = 0
        self._refusal_count = 0
        self._audit_failures = 0
        self._release_errors = 0

    # -- introspection ----------------------------------------------------- #
    @property
    def monitor(self) -> Any:
        """The injected telemetry monitor."""
        return self._monitor

    @property
    def scheduler(self) -> Any:
        """The injected resource scheduler."""
        return self._scheduler

    # -- admission --------------------------------------------------------- #
    def admit(
        self,
        task_id: str,
        *,
        requirements: Optional[ResourceRequirements] = None,
        priority: int = 0,
    ) -> Admission:
        """Decide whether *task_id* may run now.

        Returns an :class:`Admission`; it never raises for an expected
        resource/telemetry refusal.  ``ValueError`` / ``TypeError`` are raised
        only for invalid caller input.
        """
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string.")
        task_id = task_id.strip()
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError("priority must be an integer.")
        if requirements is None:
            requirements = ResourceRequirements()
        if not isinstance(requirements, ResourceRequirements):
            raise TypeError(
                "requirements must be a ResourceRequirements instance."
            )

        with self._lock:
            existing = self._admitted.get(task_id)
            if existing is not None:
                admission = Admission(
                    admitted=True,
                    reason="Task is already admitted.",
                    task_id=task_id,
                    slot=existing.slot,
                    code=existing.code,
                    degraded=existing.degraded,
                    telemetry_available=existing.telemetry_available,
                )
                self._audit_event(
                    "resource.admission", "success", admission, requirements,
                    priority,
                )
                return admission

            if len(self._admitted) >= self._max_agents:
                admission = Admission(
                    admitted=False,
                    reason=(
                        "Concurrency ceiling reached: "
                        f"{self._max_agents} agent slot(s) already admitted."
                    ),
                    task_id=task_id,
                    slot=None,
                    code=CONCURRENCY_LIMIT,
                )
                self._refusal_count += 1
                self._audit_event(
                    "resource.admission", "refused", admission, requirements,
                    priority,
                )
                return admission

            snapshot: Optional[ResourceSnapshot] = None
            sample_error: Optional[str] = None
            try:
                candidate = self._monitor.sample()
            except Exception as exc:  # noqa: BLE001 - surfaced, never hidden
                sample_error = (
                    "Resource telemetry could not be sampled "
                    f"({type(exc).__name__}: {_brief(exc)})."
                )
            else:
                if isinstance(candidate, ResourceSnapshot):
                    snapshot = candidate
                else:
                    sample_error = (
                        "Resource telemetry returned an unexpected snapshot "
                        f"type ({type(candidate).__name__})."
                    )

            if snapshot is None:
                return self._without_telemetry(
                    task_id, requirements, priority,
                    sample_error or "Resource telemetry is unavailable.",
                )

            capability_error = self._telemetry_capability_reason()
            if capability_error is not None:
                return self._without_telemetry(
                    task_id, requirements, priority, capability_error
                )

            pressure_error = self._pressure_reason(snapshot, requirements)
            if pressure_error is not None:
                admission = Admission(
                    admitted=False,
                    reason=pressure_error,
                    task_id=task_id,
                    slot=None,
                    code=RESOURCE_EXHAUSTED,
                )
                self._refusal_count += 1
                self._audit_event(
                    "resource.admission", "refused", admission, requirements,
                    priority, snapshot,
                )
                return admission

            return self._schedule(task_id, requirements, priority, snapshot)

    def can_admit(self, task_id: str = "") -> Admission:
        """Report whether one more agent slot is free, without reserving it.

        This is the non-mutating probe :class:`AgentSelector` looks for.  The
        authoritative reservation stays :meth:`admit`, which the executor
        performs for the step it is about to run.  If selection *consumed* a
        slot as well, every selected agent would leak one and the governor
        would refuse the very step it had just approved.
        """
        with self._lock:
            active = len(self._admitted)
            admitted = active < self._max_agents
            return Admission(
                admitted=admitted,
                reason=(
                    "A concurrent-agent slot is available."
                    if admitted
                    else "The concurrent-agent ceiling is reached."
                ),
                task_id=str(task_id or ""),
                slot=None,
                code=ADMITTED if admitted else CONCURRENCY_LIMIT,
            )

    def admit_strict(
        self,
        task_id: str,
        *,
        requirements: Optional[ResourceRequirements] = None,
        priority: int = 0,
    ) -> Admission:
        """Like :meth:`admit` but raises a typed error when refused."""
        admission = self.admit(
            task_id, requirements=requirements, priority=priority
        )
        if admission.admitted:
            return admission
        if admission.code == CONCURRENCY_LIMIT:
            raise ConcurrencyLimitError(
                admission.reason, task_id=task_id
            )
        if admission.code == TELEMETRY_UNAVAILABLE:
            raise TelemetryUnavailableError(
                admission.reason, task_id=task_id
            )
        raise ResourceExhaustedError(admission.reason, task_id=task_id)

    # -- release ----------------------------------------------------------- #
    def release(self, task_id: str, *, success: bool = True) -> None:
        """Free the slot held by *task_id* (idempotent).

        An unknown or already released task is ignored: it must never corrupt
        the slot accounting of the tasks that are still running.
        """
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string.")
        task_id = task_id.strip()

        with self._lock:
            admission = self._admitted.pop(task_id, None)
            self._requirements.pop(task_id, None)
            if admission is None:
                return

            if not admission.degraded and admission.telemetry_available:
                try:
                    self._scheduler.complete(task_id, success=bool(success))
                except (SchedulerError, UnknownTaskError):
                    # The scheduler state diverged (for example an external
                    # completion); the slot is still released and the anomaly
                    # is reported through status().
                    self._release_errors += 1

            outcome = "success" if success else "failure"
            details = {
                "task_id": task_id,
                "slot": admission.slot,
                "success": bool(success),
                "degraded": admission.degraded,
            }
            self._audit_record("resource.release", outcome, details)

    # -- subprocess ceiling ------------------------------------------------ #
    def try_subprocess_slot(self) -> bool:
        """Reserve one subprocess slot, or return ``False`` at the ceiling."""
        with self._lock:
            if self._subprocess_in_use >= self._max_subprocesses:
                return False
            self._subprocess_in_use += 1
            return True

    def release_subprocess_slot(self) -> None:
        """Return one subprocess slot; never goes below zero."""
        with self._lock:
            if self._subprocess_in_use > 0:
                self._subprocess_in_use -= 1

    # -- telemetry --------------------------------------------------------- #
    def require_telemetry(self) -> ResourceSnapshot:
        """Return a fresh snapshot or raise ``TelemetryUnavailableError``."""
        try:
            snapshot = self._monitor.sample()
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed error
            raise TelemetryUnavailableError(
                "Required resource telemetry could not be sampled.",
                error=type(exc).__name__,
            ) from exc
        if not isinstance(snapshot, ResourceSnapshot):
            raise TelemetryUnavailableError(
                "Resource telemetry returned an unexpected snapshot type.",
                returned=type(snapshot).__name__,
            )
        reason = self._telemetry_capability_reason()
        if reason is not None:
            raise TelemetryUnavailableError(reason)
        return snapshot

    # -- status ------------------------------------------------------------ #
    def status(self) -> dict[str, Any]:
        """Return a snapshot of governor state.  Never raises."""
        try:
            with self._lock:
                slots = {
                    task_id: admission.slot
                    for task_id, admission in sorted(self._admitted.items())
                }
                return {
                    "max_concurrent_agents": self._max_agents,
                    "active_agents": len(self._admitted),
                    "available_agent_slots": max(
                        0, self._max_agents - len(self._admitted)
                    ),
                    "admitted_tasks": slots,
                    "subprocesses": {
                        "in_use": self._subprocess_in_use,
                        "max": self._max_subprocesses,
                        "available": max(
                            0,
                            self._max_subprocesses - self._subprocess_in_use,
                        ),
                    },
                    "telemetry": self._telemetry_status(),
                    "scheduler": self._scheduler_status(),
                    "degraded": self._is_degraded(),
                    "counters": {
                        "admissions": self._admission_count,
                        "refusals": self._refusal_count,
                        "audit_failures": self._audit_failures,
                        "release_errors": self._release_errors,
                    },
                }
        except Exception as exc:  # pragma: no cover - defensive contract
            return {
                "error": type(exc).__name__,
                "max_concurrent_agents": self._max_agents,
                "active_agents": None,
            }

    # -- internal: admission paths ----------------------------------------- #
    def _schedule(
        self,
        task_id: str,
        requirements: ResourceRequirements,
        priority: int,
        snapshot: ResourceSnapshot,
    ) -> Admission:
        try:
            self._scheduler.update_resources(snapshot)
        except Exception as exc:  # noqa: BLE001 - mapped to a refusal
            admission = Admission(
                admitted=False,
                reason=(
                    "Resource scheduler rejected the telemetry snapshot "
                    f"({type(exc).__name__})."
                ),
                task_id=task_id,
                slot=None,
                code=RESOURCE_EXHAUSTED,
            )
            self._refusal_count += 1
            self._audit_event(
                "resource.admission", "refused", admission, requirements,
                priority, snapshot,
            )
            return admission

        try:
            self._ensure_submitted(task_id, requirements, priority)
        except _SubmissionRefused as refusal:
            admission = Admission(
                admitted=False,
                reason=refusal.reason,
                task_id=task_id,
                slot=None,
                code=refusal.code,
            )
            self._refusal_count += 1
            self._audit_event(
                "resource.admission", "refused", admission, requirements,
                priority, snapshot,
            )
            return admission

        dispatched: Any = None
        try:
            dispatched = self._scheduler.dispatch_next()
        except Exception as exc:  # noqa: BLE001 - mapped to a refusal
            admission = Admission(
                admitted=False,
                reason=(
                    "Resource scheduler could not dispatch the task "
                    f"({type(exc).__name__}: {_brief(exc)})."
                ),
                task_id=task_id,
                slot=None,
                code=RESOURCE_EXHAUSTED,
            )
            self._refusal_count += 1
            self._audit_event(
                "resource.admission", "refused", admission, requirements,
                priority, snapshot,
            )
            return admission

        if dispatched is not None and getattr(dispatched, "task_id", None) == task_id:
            slot = self._next_slot()
            if slot is None:  # pragma: no cover - defensive accounting guard
                try:
                    self._scheduler.complete(task_id, success=False)
                except (SchedulerError, UnknownTaskError):
                    pass
                admission = Admission(
                    admitted=False,
                    reason="No free agent slot could be allocated.",
                    task_id=task_id,
                    slot=None,
                    code=CONCURRENCY_LIMIT,
                )
                self._refusal_count += 1
                self._audit_event(
                    "resource.admission", "refused", admission, requirements,
                    priority, snapshot,
                )
                return admission

            admission = Admission(
                admitted=True,
                reason="Admission granted by the resource scheduler.",
                task_id=task_id,
                slot=slot,
                code=ADMITTED,
                degraded=False,
                telemetry_available=True,
            )
            self._admitted[task_id] = admission
            self._requirements[task_id] = requirements
            self._admission_count += 1
            self._audit_event(
                "resource.admission", "success", admission, requirements,
                priority, snapshot,
            )
            return admission

        reason = "Resource requirements are not satisfied right now."
        scheduled = self._safe_get_task(task_id)
        if scheduled is not None and getattr(scheduled, "reason", ""):
            reason = str(scheduled.reason)
        if dispatched is not None:
            reason = (
                "Another queued task "
                f"({getattr(dispatched, 'task_id', 'unknown')}) was dispatched "
                f"first; {reason}"
            )
        admission = Admission(
            admitted=False,
            reason=reason,
            task_id=task_id,
            slot=None,
            code=RESOURCE_EXHAUSTED,
        )
        self._refusal_count += 1
        self._audit_event(
            "resource.admission", "refused", admission, requirements,
            priority, snapshot,
        )
        return admission

    def _without_telemetry(
        self,
        task_id: str,
        requirements: ResourceRequirements,
        priority: int,
        reason: str,
    ) -> Admission:
        """Refuse (fail-closed) or admit degraded when telemetry is missing."""
        if bool(self.config.fail_closed_without_telemetry):
            admission = Admission(
                admitted=False,
                reason=(
                    f"{reason} Admission refused: "
                    "fail_closed_without_telemetry is enabled."
                ),
                task_id=task_id,
                slot=None,
                code=TELEMETRY_UNAVAILABLE,
                telemetry_available=False,
            )
            self._refusal_count += 1
            self._audit_event(
                "resource.admission", "refused", admission, requirements,
                priority,
            )
            return admission

        slot = self._next_slot()
        if slot is None:
            admission = Admission(
                admitted=False,
                reason=(
                    "Concurrency ceiling reached: "
                    f"{self._max_agents} agent slot(s) already admitted."
                ),
                task_id=task_id,
                slot=None,
                code=CONCURRENCY_LIMIT,
                telemetry_available=False,
            )
            self._refusal_count += 1
            self._audit_event(
                "resource.admission", "refused", admission, requirements,
                priority,
            )
            return admission

        admission = Admission(
            admitted=True,
            reason=(
                f"{reason} Admitted in degraded mode: the resource scheduler "
                "is bypassed because no trustworthy snapshot exists."
            ),
            task_id=task_id,
            slot=slot,
            code=DEGRADED,
            degraded=True,
            telemetry_available=False,
        )
        self._admitted[task_id] = admission
        self._requirements[task_id] = requirements
        self._admission_count += 1
        self._audit_event(
            "resource.admission", "degraded", admission, requirements, priority
        )
        return admission

    def _ensure_submitted(
        self,
        task_id: str,
        requirements: ResourceRequirements,
        priority: int,
    ) -> None:
        """Submit and approve *task_id*, reusing an existing queued record."""
        try:
            self._scheduler.submit(task_id, requirements, priority)
        except DuplicateTaskError:
            scheduled = self._safe_get_task(task_id)
            if scheduled is None:
                raise _SubmissionRefused(
                    RESOURCE_EXHAUSTED,
                    "Resource scheduler lost the existing task record.",
                ) from None
            if getattr(scheduled, "requirements", None) != requirements:
                raise _SubmissionRefused(
                    RESOURCE_EXHAUSTED,
                    "A task with the same id is already queued with "
                    "different requirements.",
                ) from None
            state = getattr(scheduled, "state", None)
            if state == TaskState.RUNNING:
                raise _SubmissionRefused(
                    CONCURRENCY_LIMIT,
                    "The task is already running in the resource scheduler.",
                ) from None
            if state in (
                TaskState.COMPLETED,
                TaskState.FAILED,
                TaskState.REJECTED,
            ):
                raise _SubmissionRefused(
                    RESOURCE_EXHAUSTED,
                    f"The task previously ended in state {state.value}.",
                ) from None
            if state == TaskState.WAITING_APPROVAL:
                try:
                    self._scheduler.mark_approved(task_id)
                except SchedulerError as exc:
                    raise _SubmissionRefused(
                        RESOURCE_EXHAUSTED,
                        "Resource scheduler refused to approve the task "
                        f"({type(exc).__name__}).",
                    ) from exc
            return
        except SchedulerError as exc:
            raise _SubmissionRefused(
                RESOURCE_EXHAUSTED,
                "Resource scheduler refused the task "
                f"({type(exc).__name__}: {_brief(exc)}).",
            ) from exc

        try:
            self._scheduler.mark_approved(task_id)
        except SchedulerError as exc:
            raise _SubmissionRefused(
                RESOURCE_EXHAUSTED,
                "Resource scheduler refused to approve the task "
                f"({type(exc).__name__}: {_brief(exc)}).",
            ) from exc

    # -- internal: helpers -------------------------------------------------- #
    def _next_slot(self) -> Optional[int]:
        used = {
            admission.slot
            for admission in self._admitted.values()
            if admission.slot is not None
        }
        for index in range(self._max_agents):
            if index not in used:
                return index
        return None

    def _safe_get_task(self, task_id: str) -> Any:
        try:
            return self._scheduler.get_task(task_id)
        except Exception:  # noqa: BLE001 - unknown task is reported as None
            return None

    def _pressure_reason(
        self,
        snapshot: ResourceSnapshot,
        requirements: ResourceRequirements,
    ) -> Optional[str]:
        cpu_limit = float(self.config.max_cpu_load_percent)
        if snapshot.cpu_load_percent > cpu_limit:
            return (
                f"CPU load {snapshot.cpu_load_percent:.1f}% exceeds the "
                f"configured admission limit of {cpu_limit:.1f}%."
            )

        reserve = float(self.config.min_free_ram_gb)
        required_ram = float(requirements.ram_gb) + reserve
        if snapshot.ram_available_gb < required_ram:
            return (
                f"Available RAM {snapshot.ram_available_gb:.2f} GB is below "
                f"the required {required_ram:.2f} GB "
                f"({float(requirements.ram_gb):.2f} GB task + "
                f"{reserve:.2f} GB reserve)."
            )

        if float(requirements.ram_gb) > float(self.config.max_process_ram_gb):
            return (
                f"Task RAM requirement {float(requirements.ram_gb):.2f} GB "
                "exceeds the per-process ceiling of "
                f"{float(self.config.max_process_ram_gb):.2f} GB."
            )

        if self.config.thermal_limit_c is not None:
            if snapshot.temperature_c is None:
                return (
                    "A thermal admission limit is configured but temperature "
                    "telemetry is unavailable; admission refused."
                )
            if snapshot.temperature_c >= float(self.config.thermal_limit_c):
                return (
                    f"Temperature {snapshot.temperature_c:.1f} C reached the "
                    f"configured limit of {float(self.config.thermal_limit_c):.1f} C."
                )

        if requirements.max_temperature_c is not None:
            if snapshot.temperature_c is None:
                return (
                    "The task requires temperature telemetry which is "
                    "unavailable; admission refused."
                )
            if snapshot.temperature_c > float(requirements.max_temperature_c):
                return (
                    f"Temperature {snapshot.temperature_c:.1f} C exceeds the "
                    f"task limit of {float(requirements.max_temperature_c):.1f} C."
                )
        return None

    def _telemetry_capability_reason(self) -> Optional[str]:
        """Return why telemetry must be considered unavailable, else ``None``."""
        capabilities = getattr(self._monitor, "capabilities", None)
        if callable(capabilities):
            try:
                report = capabilities()
            except Exception as exc:  # noqa: BLE001
                return (
                    "Resource telemetry capabilities could not be read "
                    f"({type(exc).__name__})."
                )
            if isinstance(report, Mapping):
                missing = sorted(
                    str(key) for key, value in report.items() if not value
                )
                if missing:
                    return (
                        "Resource telemetry metrics are unavailable: "
                        + ", ".join(missing)
                        + "."
                    )
        return self._staleness_reason()

    def _staleness_reason(self) -> Optional[str]:
        age_fn = getattr(self._monitor, "age", None)
        if not callable(age_fn):
            return None
        try:
            age = age_fn()
        except Exception as exc:  # noqa: BLE001
            return (
                "Resource telemetry age could not be read "
                f"({type(exc).__name__})."
            )
        if age is None:
            return "Resource telemetry has never been sampled."
        limit = getattr(
            self._monitor,
            "max_age_seconds",
            self.config.telemetry_max_age_seconds,
        )
        try:
            limit_value = float(limit)
        except (TypeError, ValueError):
            limit_value = float(self.config.telemetry_max_age_seconds)
        if float(age) > limit_value:
            return (
                f"Resource telemetry is stale ({float(age):.1f}s old, "
                f"limit {limit_value:.1f}s)."
            )
        return None

    def _telemetry_status(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "available": None,
            "age_seconds": None,
            "capabilities": {},
            "fail_closed": bool(self.config.fail_closed_without_telemetry),
        }
        try:
            capabilities = getattr(self._monitor, "capabilities", None)
            if callable(capabilities):
                report = capabilities()
                if isinstance(report, Mapping):
                    status["capabilities"] = {
                        str(key): bool(value) for key, value in report.items()
                    }
        except Exception as exc:  # noqa: BLE001 - status must never raise
            status["error"] = type(exc).__name__

        age_observed = False
        try:
            age_fn = getattr(self._monitor, "age", None)
            if callable(age_fn):
                age_observed = True
                age = age_fn()
                status["age_seconds"] = None if age is None else float(age)
        except Exception as exc:  # noqa: BLE001 - status must never raise
            status.setdefault("error", type(exc).__name__)

        capability_map = status["capabilities"]
        if capability_map:
            status["available"] = all(capability_map.values())
            if status["available"] and age_observed and status["age_seconds"] is None:
                # Introspectable monitor that has never been sampled: the
                # platform supports the metrics but no reading exists yet.
                status["available"] = False
        return status

    def _scheduler_status(self) -> dict[str, Any]:
        try:
            report = self._scheduler.status()
        except Exception as exc:  # noqa: BLE001 - status must never raise
            return {"available": False, "error": type(exc).__name__}
        if isinstance(report, Mapping):
            merged = {"available": True}
            merged.update({str(key): value for key, value in report.items()})
            return merged
        return {"available": True, "raw": str(report)}

    def _is_degraded(self) -> bool:
        try:
            unavailable_fn = getattr(self._monitor, "unavailable_metrics", None)
            if callable(unavailable_fn):
                return bool(unavailable_fn())
            capabilities = self._telemetry_status().get("capabilities") or {}
            return any(not value for value in capabilities.values())
        except Exception:  # noqa: BLE001 - unknown state is degraded state
            return True

    def _audit_event(
        self,
        action: str,
        outcome: str,
        admission: Admission,
        requirements: ResourceRequirements,
        priority: int,
        snapshot: Optional[ResourceSnapshot] = None,
    ) -> None:
        details: dict[str, Any] = {
            "task_id": admission.task_id,
            "admitted": admission.admitted,
            "code": admission.code,
            "slot": admission.slot,
            "reason": admission.reason,
            "priority": priority,
            "degraded": admission.degraded,
            "requirements": {
                "ram_gb": float(requirements.ram_gb),
                "cpu_cores": float(requirements.cpu_cores),
                "gpu_memory_gb": float(requirements.gpu_memory_gb),
                "model_name": requirements.model_name,
            },
        }
        if snapshot is not None:
            details["snapshot"] = {
                "cpu_load_percent": float(snapshot.cpu_load_percent),
                "ram_available_gb": float(snapshot.ram_available_gb),
                "logical_cpu_count": int(snapshot.logical_cpu_count),
            }
        self._audit_record(action, outcome, details)

    def _audit_record(
        self, action: str, outcome: str, details: dict[str, Any]
    ) -> None:
        if self._audit is None:
            return
        try:
            self._audit.record(
                action=action,
                actor="resource_governor",
                outcome=outcome,
                details=details,
            )
        except Exception:  # noqa: BLE001 - counted, never corrupts a decision
            self._audit_failures += 1


def _brief(exc: BaseException, limit: int = 160) -> str:
    """Return a short, single-line rendering of an exception message."""
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


__all__ = [
    "ADMITTED",
    "Admission",
    "CONCURRENCY_LIMIT",
    "DEGRADED",
    "RESOURCE_EXHAUSTED",
    "ResourceGovernor",
    "TELEMETRY_UNAVAILABLE",
]
