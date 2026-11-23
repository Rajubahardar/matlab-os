"""Controlled multi-agent collaboration layer for Phase 9 (staging only).

Public surface of the ``matlab_os.phase9.collaboration`` package.  Importing
this package performs no I/O, network access or model loading.
"""

from __future__ import annotations

from .coordinator import (
    AGGREGATION_COMPLETED,
    AGGREGATION_CONFLICT,
    AGGREGATION_FAILED,
    AGGREGATION_PARTIAL,
    DELEGATION_ACCEPTED,
    DELEGATION_CANCELLED,
    DELEGATION_COMPLETED,
    DELEGATION_FAILED,
    DELEGATION_PENDING,
    DELEGATION_STATUSES,
    DELEGATION_TIMEOUT,
    AggregationResult,
    CollaborationCoordinator,
    DelegationRecord,
)
from .protocol import SCHEMA_VERSION, AgentMessage, MessageKind

__all__ = [
    "SCHEMA_VERSION",
    "MessageKind",
    "AgentMessage",
    "DelegationRecord",
    "AggregationResult",
    "CollaborationCoordinator",
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
]
