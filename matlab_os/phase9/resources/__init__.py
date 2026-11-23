"""Resource monitoring and admission control for Phase 9 (staging only).

The package exports the stdlib telemetry monitor and the resource governor that
performs admission control through the reused
:class:`matlab_os.intelligence.resource_scheduler.ResourceScheduler`.

Attributes are re-exported eagerly from their defining modules; the module is
small, performs no OS sampling or I/O at import time, and every name it does
not own raises :class:`AttributeError`.
"""

from __future__ import annotations

from typing import Any

from .governor import Admission, ResourceGovernor
from .monitor import (
    CAPABILITY_KEYS,
    CONSERVATIVE_CPU_LOAD,
    CONSERVATIVE_LOGICAL_CPU_COUNT,
    CONSERVATIVE_RAM_AVAILABLE_GB,
    UNAVAILABLE,
    ResourceMonitor,
)

__all__ = [
    "Admission",
    "CAPABILITY_KEYS",
    "CONSERVATIVE_CPU_LOAD",
    "CONSERVATIVE_LOGICAL_CPU_COUNT",
    "CONSERVATIVE_RAM_AVAILABLE_GB",
    "ResourceGovernor",
    "ResourceMonitor",
    "UNAVAILABLE",
]


def __getattr__(name: str) -> Any:  # pragma: no cover - import plumbing
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:  # pragma: no cover - introspection helper
    return sorted(set(__all__) | set(globals()))
