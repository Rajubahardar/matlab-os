"""Model registry for Phase 13 universal model intelligence.

The registry is the provider-independent catalog that Phase 13 routing selects from.
It is intentionally in-memory for the first vertical slice; persistence, cost/context
metadata enrichment, and hardware-aware placement come later.
"""

from __future__ import annotations

from .registry import (
    ModelProfile,
    ModelRegistry,
    SelectionContext,
    UnknownModelError,
)

__all__ = [
    "ModelProfile",
    "ModelRegistry",
    "SelectionContext",
    "UnknownModelError",
]
