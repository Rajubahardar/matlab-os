"""Common integration contract (Phase 11).

The single interface every integration must implement. It is deliberately
small: identity, lifecycle, capability discovery and health. Policy, approval,
schema validation and audit live in the framework layer that consumes the
contract, so an integration advertises what it can do and a framework decides
whether and how that capability may be used.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping, Iterable

#: Integration lifecycle states, in order.
IntegrationState: typeAlias = str
#: A capability record produced by a connected integration.
CapabilityRecord: typeAlias = dict[str, Any]


class IntegrationError(RuntimeError):
    """Base error for the integration framework."""


class IntegrationNotReadyError(IntegrationError):
    """The integration is connected but not usable (not connected, broken, etc)."""


class DuplicateCapabilityError(IntegrationError):
    """A declared capability already exists in the registry."""


class UnknownCapabilityError(IntegrationError):
    """A requested capability was not declared by the integration."""


class InvalidConfigurationError(IntegrationError):
    """The integration configuration failed validation."""


class IntegrationStateError(IntegrationError):
    """The integration is in a state that does not allow the requested operation."""


class Contract(ABC):
    """Common interface for every integration.

    Implementations are **protocol agnostic**. They discover and describe what
    an external service offers; they never decide policy. Policy, approval and
    audit are applied by the framework layer that consumes this contract.
    """

    #: Integration lifecycle states, in order.
    IntegrationState: typeAlias = str
    #: A capability record produced by a connected integration.
    CapabilityRecord: typeAlias = dict[str, Any]

    # -- identity ---------------------------------------------------------- #

    @property
    @abstractmethod
    def integration_id(self) -> str:
        """Stable, machine readable identifier (for example ``matlab-mcp``)."""

    @property
    def name(self) -> str:
        """Human readable name."""
        return ""

    def version(self) -> str:
        """Integration version from configuration, not from the remote."""
        return "1.0"

    @property
    def protocol(self) -> str:
        """Canonical protocol name (for example ``stdio``)."""
        return None

    @property
    def protocol_version(self) -> str:
        """Protocol version negotiated with the remote, if any."""
        return "2024-11-05"

    # -- metadata ---------------------------------------------------------- #

    @property
    @abstractmethod
    def description(self) -> str:
        """Short description of what this integration provides."""

    @property
    @abstractmethod
    def capabilities(self) -> tuple[str, ...]:
        """Declared capability ids, in declaration order."""

    # -- configuration --------------------------------------------------- #

    @abstractmethod
    def validate_configuration(self) -> None:
        """Raise :class:`InvalidConfigurationError` when the configuration is
        unusable. Never mutates connection state."""

    # -- permissions ----------------------------------------------------- #

    @property
    @abstractmethod
    def requires_approval(self) -> tuple[str, ...]:
        """Capability ids that require a human approval ticket before use."""

    @property
    @abstractmethod
    def requires_permission(self) -> tuple[str, ...]:
        """Capability ids that require an explicit permission grant."""

    # -- lifecycle ------------------------------------------------------- #

    @abstractmethod
    def connect(self, timeout: float = 30.0) -> None:
        """Establish the underlying transport and refresh capability state."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True once connect() has succeeded and nothing was torn down."""

    @abstractmethod
    def disconnect(self) -> None:
        """Cleanly tear down the transport and free resources."""

    @abstractmethod
    def close(self) -> None:
        """Release all resources; idempotent."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Live health snapshot. Never raises: failures become ``unhealthy``."""

    # -- capability discovery -------------------------------------------- #

    @abstractmethod
    def discover_capabilities(self, timeout: float = 30.0) -> Iterable[CapabilityRecord]:
        """Return one record per discovered tool / operation.

        Records are normalised, validated representations of external metadata.
        External descriptions are untrusted: they are validated against the
        contract before they can be indexed.
        """

    def capabilities(self) -> tuple[str, ...]:
        # Base contract preserves the declared capability set unchanged.
        return self._capabilities
