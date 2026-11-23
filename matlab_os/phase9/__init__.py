"""Phase 9 — Intelligent Development (staging).

Public API surface.  Import order matters only for readability: this module
re-exports the stable, documented entry points and never triggers I/O, network
access or model loading on import.
"""

from __future__ import annotations

__version__ = "9.0.0-staging"
__phase__ = "Phase 9 - Intelligent Development"
__status__ = "STAGING_ONLY"

from .errors import (  # noqa: E402
    AgentSelectionError,
    ApprovalRejectedError,
    ApprovalRequiredError,
    BackupError,
    CollaborationError,
    ConfigurationError,
    ConflictError,
    DelegationCycleError,
    DelegationDepthExceededError,
    IntegrityError,
    PathSecurityError,
    PermissionDeniedError,
    Phase9Error,
    PlanValidationError,
    PlanningError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
    ResourceExhaustedError,
    SecurityViolation,
    WorkspaceBoundaryError,
    WorkspaceError,
)

__all__ = [
    "__version__",
    "__phase__",
    "__status__",
    "Phase9Error",
    "ConfigurationError",
    "SecurityViolation",
    "PathSecurityError",
    "PermissionDeniedError",
    "ApprovalRequiredError",
    "ApprovalRejectedError",
    "WorkspaceError",
    "WorkspaceBoundaryError",
    "IntegrityError",
    "BackupError",
    "ProviderError",
    "ProviderUnavailableError",
    "ProviderNotConfiguredError",
    "PlanningError",
    "PlanValidationError",
    "AgentSelectionError",
    "CollaborationError",
    "ConflictError",
    "DelegationDepthExceededError",
    "DelegationCycleError",
    "ResourceExhaustedError",
]
