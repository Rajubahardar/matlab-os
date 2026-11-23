"""Resource scheduling primitives required by the current branch.

This package provides the scheduler surface that current code imports from
``matlab_os.intelligence.resource_scheduler``:

* dataclasses: ``ResourceSnapshot``, ``ResourceRequirements``
* enum: ``TaskState``
* scheduler: ``ResourceScheduler`` with ``update_resources``,
  ``submit``, ``mark_approved``, ``dispatch_next``, ``complete``,
  ``get_task``, ``status``
* errors: ``SchedulerError``, ``DuplicateTaskError``, ``UnknownTaskError``

Note on provenance
------------------
This is a Phase-13 recovery implementation of the *currently required* scheduler
surface on this branch. It is **not** a reconstruction of any historically
protected baseline file and does not claim to match any prior SHA-256 baseline.
The status of the historically protected production files is documented
separately in the blocker investigation evidence under ``staging/`` and in
``docs/roadmap/``.
"""

from __future__ import annotations

from .scheduler import (
    DuplicateTaskError,
    ResourceRequirements,
    ResourceScheduler,
    ResourceSnapshot,
    SchedulerError,
    TaskState,
    UnknownTaskError,
)

__all__ = [
    "DuplicateTaskError",
    "ResourceRequirements",
    "ResourceScheduler",
    "ResourceSnapshot",
    "SchedulerError",
    "TaskState",
    "UnknownTaskError",
]
