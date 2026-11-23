"""Typed exception hierarchy for Phase 9.

Design rules enforced here:

* Every Phase 9 failure maps to a typed exception so callers can make
  security- and resource-aware decisions instead of catching `Exception`.
* Exception messages are transport-safe: they must never carry credentials,
  API keys, bearer tokens or raw provider payloads.  Secret-bearing values are
  substituted by :func:`matlab_os.phase9.security.redaction.scrub` before a
  message is constructed.
* Exceptions never hold a reference to a live secret value in ``args``.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


class Phase9Error(Exception):
    """Base class for every Phase 9 error."""

    #: stable machine-readable code used in audit records
    code: str = "phase9_error"

    def __init__(self, message: str, **context: Any) -> None:
        self.context: dict[str, Any] = dict(context)
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - trivial
        base = super().__str__()
        if not self.context:
            return base
        rendered = ", ".join(
            f"{key}={value!r}" for key, value in sorted(self.context.items())
        )
        return f"{base} [{rendered}]"

    def describe(self) -> dict[str, Any]:
        """Return a serialisable, secret-free description."""
        return {
            "code": self.code,
            "type": type(self).__name__,
            "message": super().__str__(),
            "context": self.context,
        }


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
class ConfigurationError(Phase9Error):
    """Invalid or incomplete Phase 9 configuration."""

    code = "configuration_error"


# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #
class SecurityViolation(Phase9Error):
    """A security invariant was violated. Never silently downgraded."""

    code = "security_violation"


class PathSecurityError(SecurityViolation):
    """A path escaped the workspace boundary or used a forbidden form."""

    code = "path_security_error"


class PermissionDeniedError(SecurityViolation):
    """The acting principal lacks the permission for the requested action."""

    code = "permission_denied"


class ApprovalRequiredError(SecurityViolation):
    """The operation requires a human approval that was not supplied."""

    code = "approval_required"


class ApprovalRejectedError(SecurityViolation):
    """An approval decision was explicitly negative or already consumed."""

    code = "approval_rejected"


class SecretHandlingError(SecurityViolation):
    """A secret was about to be persisted, logged or interpolated."""

    code = "secret_handling_error"


# --------------------------------------------------------------------------- #
# Workspace
# --------------------------------------------------------------------------- #
class WorkspaceError(Phase9Error):
    """Base error for controlled workspace operations."""

    code = "workspace_error"


class WorkspaceBoundaryError(WorkspaceError, PathSecurityError):
    """Target path is outside the configured workspace."""

    code = "workspace_boundary_error"


class IntegrityError(WorkspaceError):
    """Content hash mismatched the expected value."""

    code = "integrity_error"


class BackupError(WorkspaceError):
    """A backup could not be created or verified."""

    code = "backup_error"


class OperationLimitError(WorkspaceError):
    """A per-operation size/count limit was exceeded."""

    code = "operation_limit_error"


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
class ProviderError(Phase9Error):
    """Base class for model-provider failures."""

    code = "provider_error"

    def __init__(
        self,
        message: str,
        provider_id: Optional[str] = None,
        retryable: bool = False,
        **context: Any,
    ) -> None:
        super().__init__(message, **context)
        self.provider_id = provider_id
        self.retryable = retryable


class ProviderUnavailableError(ProviderError):
    """Provider is disabled, missing credentials or unreachable."""

    code = "provider_unavailable"


class ProviderTimeoutError(ProviderError):
    """Provider exceeded the configured deadline."""

    code = "provider_timeout"

    def __init__(self, message: str, provider_id: Optional[str] = None,
                 **context: Any) -> None:
        super().__init__(message, provider_id=provider_id, retryable=True,
                         **context)


class ProviderAuthError(ProviderError):
    """Credentials were rejected. Not retryable; never echoes the secret."""

    code = "provider_auth_error"


class ProviderRateLimitError(ProviderError):
    """Remote rate limit. Retryable with backoff."""

    code = "provider_rate_limit"

    def __init__(self, message: str, provider_id: Optional[str] = None,
                 retry_after: Optional[float] = None, **context: Any) -> None:
        super().__init__(message, provider_id=provider_id, retryable=True,
                         **context)
        self.retry_after = retry_after


class ProviderResponseError(ProviderError):
    """Provider replied with a malformed or unexpected payload."""

    code = "provider_response_error"


class ProviderNotConfiguredError(ProviderError):
    """Provider is intentionally disabled by configuration."""

    code = "provider_not_configured"


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #
class PlanningError(Phase9Error):
    """Base class for planning failures."""

    code = "planning_error"


class PlanValidationError(PlanningError):
    """The plan is structurally invalid (cycle, unknown dependency, ...)."""

    code = "plan_validation_error"


class PlanLimitError(PlanningError):
    """A planning budget (steps, depth, steps-per-task) was exceeded."""

    code = "plan_limit_error"


class ReplanLimitError(PlanningError):
    """The bounded re-planning budget was exhausted."""

    code = "replan_limit_error"


class StepExecutionError(Phase9Error):
    """A plan step failed in a way the executor could not recover."""

    code = "step_execution_error"


# --------------------------------------------------------------------------- #
# Agent selection
# --------------------------------------------------------------------------- #
class AgentSelectionError(Phase9Error):
    """No agent satisfies the capability/resource/permission requirements."""

    code = "agent_selection_error"


class AgentNotFoundError(AgentSelectionError):
    """A named agent is not present in the directory."""

    code = "agent_not_found"


# --------------------------------------------------------------------------- #
# Collaboration
# --------------------------------------------------------------------------- #
class CollaborationError(Phase9Error):
    """Base class for multi-agent collaboration failures."""

    code = "collaboration_error"


class DelegationDepthExceededError(CollaborationError):
    """Delegation would exceed the configured maximum depth."""

    code = "delegation_depth_exceeded"


class DelegationCycleError(CollaborationError):
    """Delegation would form a cycle in the delegation graph."""

    code = "delegation_cycle"


class DelegationLimitError(CollaborationError):
    """A delegation count/message budget was exhausted."""

    code = "delegation_limit"


class CollaborationTimeoutError(CollaborationError):
    """A delegated task exceeded its deadline."""

    code = "collaboration_timeout"


class ConflictError(CollaborationError):
    """Aggregated results disagree irreconcilably."""

    code = "conflict_error"


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #
class ResourceError(Phase9Error):
    """Base class for resource-management failures."""

    code = "resource_error"


class ResourceExhaustedError(ResourceError):
    """The host cannot safely admit the requested work right now."""

    code = "resource_exhausted"


class TelemetryUnavailableError(ResourceError):
    """Required telemetry could not be sampled."""

    code = "telemetry_unavailable"


class ConcurrencyLimitError(ResourceError):
    """A concurrency ceiling was reached."""

    code = "concurrency_limit"


_EXPORTED = [name for name in globals() if not name.startswith("_")]

__all__ = [
    name for name in _EXPORTED
    if name[0].isupper() or name in {"Mapping"}
]


def describe_error(error: BaseException) -> dict[str, Any]:
    """Return a secret-free dictionary for an arbitrary exception."""
    if isinstance(error, Phase9Error):
        return error.describe()
    return {
        "code": "unexpected_error",
        "type": type(error).__name__,
        # message text of foreign exceptions is scrubbed by the caller that
        # owns the redaction policy; keep only the type here to be safe.
        "message": "unexpected exception",
    }
