"""Controlled workspace access for Phase 9 (staging only).

This package owns the backup store.  ``WorkspaceManager`` lives in
``workspace.operations`` (maintained separately) and is deliberately **not**
imported here: importing it eagerly would make the package unusable while that
module is absent, and the package contract is to export only the names it owns.

Any other attribute access raises :class:`AttributeError`.
"""

from __future__ import annotations

from typing import Any

from .backup import BackupRecord, BackupStore

__all__ = [
    "BackupRecord",
    "BackupStore",
]


def __getattr__(name: str) -> Any:  # pragma: no cover - import plumbing
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:  # pragma: no cover - introspection helper
    return sorted(set(__all__) | set(globals()))
