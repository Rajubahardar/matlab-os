"""Phase 11 integration registry.

Single authoritative registry for every integration in the Local AI OS. It holds
identity, configuration, declared and discovered capabilities, availability and
lifecycle state. It does not decide policy: permissions, approvals and audit are
injected separately and consulted before transport is touched.
"""
from __future__ import annotations

import threading
from typing import Any, Iterable, Iterator

from matlab_os.integrations.contract import (
    Contract,
    IntegrationError,
    IntegrationNotReadyError,
    IntegrationStateError,
    InvalidConfigurationError,
)


class RegistryError(IntegrationError):
    """The registry itself cannot be used as configured."""


class Registry:
    """Authoritative integration registry.

    State is stored per integration id:

    * ``registered`` — an integration was registered (config present).
    * ``initialized`` — connect() succeeded once.
    * ``closed`` — the registry was closed.

    Availability reflects the live contract health.
    """

    def __init__(self, *, clock=None, audit=None):
        self._lock = threading.RLock()
        self._clock = clock if clock is not None else _RealClock()
        self._audit = audit
        # integration_id -> record
        self._items: dict[str, dict[str, Any]] = {}
        self._closed = False

    # ------------------------------------------------------------------ #
    # Public operations                                                #
    # ------------------------------------------------------------------ #

    def register(self, integration: Contract) -> None:
        """Register an integration by its contract object."""
        if self._closed:
            raise IntegrationStateError("Registry is closed.")
        if not isinstance(integration, Contract):
            raise TypeError("integration must implement the Contract interface.")
        if not integration.integration_id:
            raise ValueError("integration_id must not be empty.")

        with self._lock:
            if self._closed:
                raise IntegrationStateError("Registry is closed.")
            if integration.integration_id in self._items:
                raise IntegrationError(
                    f"Integration already registered: {integration.integration_id}"
                )
            self._items[integration.integration_id] = {
                "integration": integration,
                "config": None,
                "available": False,
                "lifecycle": "registered",
                "capabilities": list(integration.capabilities()),
            }
            self._emit("registered", integration_id=integration.integration_id)

    def unregister(self, integration_id: str) -> None:
        with self._lock:
            if self._closed:
                raise IntegrationStateError("Registry is closed.")
            record = self._items.pop(integration_id, None)
            if record is None:
                raise KeyError(f"Unknown integration: {integration_id}")
            self._emit("unregistered", integration_id=integration_id)

    def get(self, integration_id: str) -> Contract:
        with self._lock:
            record = self._items.get(integration_id)
            if record is None:
                raise KeyError(f"Unknown integration: {integration_id}")
            return record["integration"]

    def lookup_by_capability(self, capability: str) -> Iterator[str]:
        """Return integration ids that declare *capability*."""
        with self._lock:
            for integration_id, record in self._items.items():
                if capability in record["capabilities"]:
                    yield integration_id

    def all(self) -> Iterable[str]:
        with self._lock:
            return list(self._items.keys())

    def config(self, integration_id: str, config: Mapping[str, Any]) -> None:
        """Validate and store configuration for an integration."""
        with self._lock:
            if self._closed:
                raise IntegrationStateError("Registry is closed.")
            record = self._items.get(integration_id)
            if record is None:
                raise KeyError(f"Unknown integration: {integration_id}")
            # Config validation is delegated to the integration's own contract.
            # No schema, no rewrite: what the integration accepts is what is
            # stored, unchanged.
            try:
                record["integration"].validate_configuration()
            except Exception as exc:
                raise InvalidConfigurationError(
                    f"Configuration invalid for {integration_id}: {exc}"
                ) from exc
            record["config"] = dict(config)
            record["available"] = self._probe(record["integration"])
            self._emit("configured", integration_id=integration_id)

    def lifecycle(self, integration_id: str) -> str:
        """Return the lifecycle state for one integration."""
        with self._lock:
            record = self._items.get(integration_id)
            if record is None:
                raise KeyError(f"Unknown integration: {integration_id}")
            if self._closed:
                return "closed"
            return record["lifecycle"]

    def availability(self, integration_id: str) -> bool:
        """Return the current availability of an integration."""
        with self._lock:
            record = self._items.get(integration_id)
            if record is None:
                raise KeyError(f"Unknown integration: {integration_id}")
            if self._closed:
                return False
            return record["available"]

    def health(self, integration_id: str) -> dict[str, Any]:
        """Live health snapshot for one integration."""
        with self._lock:
            record = self._items.get(integration_id)
            if record is None:
                raise KeyError(f"Unknown integration: {integration_id}")
            contract = record["integration"]
            try:
                state = contract.health()
            except Exception as exc:  # pragma: no cover - defensive
                state = {"status": "unhealthy", "error": str(exc)}
            return {"integration_id": integration_id, **state}

    # ------------------------------------------------------------------ #
    # Lifecycle / availability                                         #
    # ------------------------------------------------------------------ #

    def initialize(self, integration_id: str, timeout: float = 30.0) -> None:
        """Connect an integration and index its capabilities."""
        with self._lock:
            record = self._items.get(integration_id)
            if record is None:
                raise KeyError(f"Unknown integration: {integration_id}")
            contract = record["integration"]
            try:
                contract.connect(timeout=timeout)
            except Exception as exc:
                record["lifecycle"] = "closed"
                record["available"] = False
                raise IntegrationNotReadyError(
                    f"Cannot initialize {integration_id}: {exc}"
                ) from exc
            if not contract.is_connected():
                raise IntegrationNotReadyError(
                    f"{integration_id} connected but reports not ready."
                )
            record["lifecycle"] = "initialized"
            record["available"] = True
            record["capabilities"] = list(contract.capabilities())
            self._emit(
                "initialized",
                integration_id=integration_id,
                capability_count=len(record["capabilities"]),
            )

    def shutdown(self, integration_id: str) -> None:
        with self._lock:
            record = self._items.get(integration_id)
            if record is None:
                raise KeyError(f"Unknown integration: {integration_id}")
            contract = record["integration"]
            try:
                contract.disconnect()
            finally:
                record["lifecycle"] = "registered"
                record["available"] = False

    # ------------------------------------------------------------------ #
    # Diagnostics                                                      #
    # ------------------------------------------------------------------ #

    def registry(self) -> dict[str, Any]:
        with self._lock:
            entries = {}
            for integration_id, record in self._items.items():
                entries[integration_id] = {
                    "lifecycle": record["lifecycle"],
                    "available": record["available"],
                    "configured": record["config"] is not None,
                    "capabilities": list(record["capabilities"]),
                }
            return entries

    # ------------------------------------------------------------------ #
    # Introspection                                                    #
    # ------------------------------------------------------------------ #

    def probes(self) -> dict[str, bool]:
        with self._lock:
            return {
                integration_id: self._probe(record["integration"])
                for integration_id, record in self._items.items()
            }

    # ------------------------------------------------------------------ #
    # Audit                                                              #
    # ------------------------------------------------------------------ #

    def _emit(self, event: str, **details: Any) -> None:
        if self._audit is not None:
            self._audit({"action": f"integration.registry.{event}", **details})

    def _probe(self, contract: Contract) -> bool:
        try:
            return bool(contract.is_connected())
        except Exception:
            return False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for integration_id, record in list(self._items.items()):
                try:
                    record["integration"].close()
                except Exception:
                    pass
            self._items.clear()
            self._emit("closed")


class _RealClock:
    def now(self) -> float:
        import time

        return time.time()
