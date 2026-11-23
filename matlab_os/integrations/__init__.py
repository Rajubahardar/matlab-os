"""Phase 11 universal integration framework.

Provides the common contract, authoritative registry, protocol adapter layer and
controlled invocation path for external tools and services.
"""
from __future__ import annotations

from matlab_os.integrations.contract import (
    CapabilityRecord,
    Contract,
    DuplicateCapabilityError,
    IntegrationError,
    IntegrationNotReadyError,
    IntegrationStateError,
    InvalidConfigurationError,
    UnknownCapabilityError,
    IntegrationState,
)
from matlab_os.integrations.discovery import (
    DiscoveryError,
    SchemaValidationError,
    normalise_tool_id,
    normalise_schema,
    validate_record,
    validate_capabilities,
)
from matlab_os.integrations.invocation import (
    ApprovalRequiredError,
    DuplicateMutationError,
    Invocation,
    InvocationError,
    PermissionDeniedError,
    SchemaValidationError as InvocationSchemaValidationError,
    UnknownCapabilityError as InvocationUnknownCapabilityError,
    UnknownIntegrationError,
)
from matlab_os.integrations.mlab_adapter import (
    MATLABAdapter,
    MATLABAdapterError,
    MATLABAdapterNotReadyError,
    MATLABAdapterUnauthorizedError,
)
from matlab_os.integrations.registry import Registry, RegistryError

__all__ = [
    "CapabilityRecord",
    "Contract",
    "DuplicateCapabilityError",
    "DiscoveryError",
    "IntegrationError",
    "IntegrationNotReadyError",
    "IntegrationState",
    "IntegrationStateError",
    "InvalidConfigurationError",
    "MATLABAdapter",
    "MATLABAdapterError",
    "MATLABAdapterNotReadyError",
    "MATLABAdapterUnauthorizedError",
    "Registry",
    "RegistryError",
    "SchemaValidationError",
    "UnknownCapabilityError",
    "ApprovalRequiredError",
    "DuplicateMutationError",
    "Invocation",
    "InvocationError",
    "PermissionDeniedError",
    "InvocationSchemaValidationError",
    "InvocationUnknownCapabilityError",
    "UnknownIntegrationError",
    "TaskRequest",
    "TaskRecord",
    "TaskResult",
    "TaskRunner",
    "normalise_tool_id",
    "normalise_schema",
    "validate_record",
    "validate_capabilities",
]
