"""Memory Package

Phase 16 unified memory: project-scoped episodic/fact memory with provenance,
retrieval/ranking, and forgetting, built on top of the existing ``DatabaseService``.
"""

from __future__ import annotations

from .database import DatabaseService
from .memory import (
    MemoryQuery,
    MemoryQueryBuilder,
    MemoryRecord,
    MemoryService,
)

__all__ = [
    "DatabaseService",
    "MemoryQuery",
    "MemoryQueryBuilder",
    "MemoryRecord",
    "MemoryService",
]
