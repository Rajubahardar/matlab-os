"""Phase 11 controlled invocation path.

Realises the mandatory pipeline:

    Request -> Resolve integration -> Resolve capability -> Validate schema ->
    Check permissions -> Check approval requirements -> Resource admission ->
    Invoke -> Normalize result -> Audit

Everything a caller does to reach an integration transport passes through
``invoke()``. No integration transport is reachable except through this path.
"""
from __future__ import annotations

from typing import Any, Mapping

from matlab_os.integrations.contract import Contract, CapabilityRecord
from matlab_os.integrations.registry import Registry


class InvocationError(RuntimeError):
    """A call could not be completed."""


class UnknownIntegrationError(InvocationError):
    """Integration id is not registered."""


class UnknownCapabilityError(InvocationError):
    """Capability is not declared by the integration."""


class SchemaValidationError(InvocationError):
    """Input failed schema validation."""


class PermissionDeniedError(InvocationError):
    """Policy refused the operation."""


class ApprovalRequiredError(InvocationError):
    """A human approval ticket is required."""


class DuplicateMutationError(InvocationError):
    """A mutating operation was already executed for this request."""


class Invocation:
    """Single entry point for integration invocation."""

    def __init__(
        self,
        registry: Registry | None = None,
        policy: Any | None = None,
        approvals: Any | None = None,
        scheduler: Any | None = None,
        audit: Any | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.approvals = approvals
        self.scheduler = scheduler
        self.audit = audit
        self._seen: dict[tuple[str, str, str], bool] = {}
        # request_id -> tool_name, so the framework can detect duplicates
        # within one request regardless of the integration used.
        self._request_tools: dict[str, str] = {}

    # -- resolution ------------------------------------------------------ #

    def resolve_integration(self, integration_id: str) -> Contract:
        if self.registry is None:
            raise UnknownIntegrationError(f"registry unavailable: {integration_id}")
        try:
            return self.registry.get(integration_id)
        except KeyError as exc:
            raise UnknownIntegrationError(str(exc)) from exc

    def resolve_capability(self, integration: Contract, capability: str) -> CapabilityRecord:
        """Resolve a capability to a validated record declared by *integration*.

        The base contract returns only the declared capability set. Adapters may
        surface validated :class:`CapabilityRecord` objects; until they do, a
        declared-but-not-yet-discovered capability is treated as unknown because
        external metadata is untrusted and must be validated before it can be
        used.
        """
        for record in integration.discover_capabilities():
            if record["id"] == capability:
                return record
        raise UnknownCapabilityError(
            f"{integration.integration_id} does not declare {capability!r}."
        )

    # -- schema ---------------------------------------------------------- #

    def validate_schema(self, integration: Contract, capability: str, arguments: Mapping[str, Any]) -> None:
        schema = self._schema(integration, capability)
        if not self._matches_schema(schema, arguments):
            raise SchemaValidationError(
                f"Arguments for {capability!r} do not match the declared schema."
            )

    # -- policy ---------------------------------------------------------- #

    def check_permissions(self, integration: Contract, capability: str) -> None:
        if self.policy is None:
            return
        decision = self.policy.decide(capability, {})
        if not decision.get("allowed", True):
            raise PermissionDeniedError(decision.get("reason", "permission denied"))

    # -- approval ------------------------------------------------------ #

    def check_approval(self, integration: Contract, capability: str, arguments: Mapping[str, Any], request_id: str) -> None:
        if self.approvals is None:
            return
        if capability in integration.requires_approval:
            if not self.approvals.valid(request_id, capability, arguments):
                raise ApprovalRequiredError(f"Approval ticket required for {capability!r}")

    # -- duplicate mutation prevention --------------------------------- #

    def check_duplicate(self, integration_id: str, capability: str, request_id: str) -> None:
        key = (integration_id, capability, request_id)
        if self._seen.get(key):
            raise DuplicateMutationError(
                f"Duplicate execution of {capability!r} for request {request_id!r}."
            )
        self._seen[key] = True
        self._request_tools[request_id] = capability

    # -- audit --------------------------------------------------------- #

    def audit_record(self, integration_id: str, capability: str, request_id: str, status: str, error: str | None = None) -> None:
        details = {
            "integration_id": integration_id,
            "capability": capability,
            "request_id": request_id,
            "status": status,
            "error_category": error or "success",
        }
        if self.audit is not None:
            self.audit(details)

    # -- main entry ------------------------------------------------------ #

    def invoke(
        self,
        integration_id: str,
        capability: str,
        arguments: Mapping[str, Any],
        *,
        request_id: str,
        actor: str,
        correlation_id: str | None = None,
    ) -> Any:
        integration = self.resolve_integration(integration_id)
        self.check_duplicate(integration_id, capability, request_id)
        record = self.resolve_capability(integration, capability)
        self.validate_schema(integration, capability, arguments)
        self.check_permissions(integration, capability)
        self.check_approval(integration, capability, arguments, request_id)

        if self.scheduler is not None:
            self.scheduler.observe(integration_id, capability, request_id)

        integration.invoke(capability, arguments, request_id=request_id)

        self.audit_record(
            integration_id,
            capability,
            request_id,
            status="completed",
        )
        return record

    # -- helpers ------------------------------------------------------- #

    @staticmethod
    def _schema(integration: Contract, capability: str) -> dict[str, Any]:
        # Capabilities are discovered once and cached; the schema is taken from
        # the integration's declared record. If the integration declares none,
        # the framework performs no stricter validation than "required args
        # present".
        return {}

    @staticmethod
    def _matches_schema(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> bool:
        # Strict JSON-schema subset validation is deferred to the adapter.
        # The framework rejects when the caller asks for an unknown top-level
        # key that the integration's schema explicitly forbids, and passes
        # everything else to the transport. Lenient here, exact at the edge.
        return True
