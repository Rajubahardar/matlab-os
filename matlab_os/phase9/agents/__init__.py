"""Agent capability, registry and selection layer for Phase 9 (staging only).

Public surface of the ``matlab_os.phase9.agents`` package.  Importing this
package performs no I/O, network access or model loading.
"""

from __future__ import annotations

from .capability import (
    LOCALITIES,
    AgentCapability,
    AgentProfile,
    AgentStatus,
)
from .directory import AgentDirectory
from .selector import (
    CAPACITY_WEIGHT,
    COMPLEXITY_WEIGHT,
    COST_WEIGHT,
    LOCALITY_WEIGHT,
    SCORE_COMPONENTS,
    SPECIFICITY_WEIGHT,
    AgentSelector,
    SelectionCandidate,
    SelectionResult,
)

__all__ = [
    "LOCALITIES",
    "AgentStatus",
    "AgentCapability",
    "AgentProfile",
    "AgentDirectory",
    "AgentSelector",
    "SelectionCandidate",
    "SelectionResult",
    "SCORE_COMPONENTS",
    "SPECIFICITY_WEIGHT",
    "COMPLEXITY_WEIGHT",
    "CAPACITY_WEIGHT",
    "LOCALITY_WEIGHT",
    "COST_WEIGHT",
]
