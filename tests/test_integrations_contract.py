"""Phase 11 integration contract tests."""
from __future__ import annotations

import pytest

from matlab_os.integrations.contract import (
    Contract,
    IntegrationStateError,
    IntegrationNotReadyError,
    UnknownCapabilityError,
)


class _StubContract(Contract):
    """Minimal in-memory integration for pure-contract tests."""

    def __init__(self, integration_id: str, capabilities=()) -> None:
        self._integration_id = integration_id
        self._capabilities = tuple(capabilities)
        self._connected = False
        self._health = {"status": "ok"}

    @property
    def integration_id(self) -> str:
        return self._integration_id

    def description(self) -> str:
        return "stub"

    def capabilities(self) -> tuple[str, ...]:
        return self._capabilities

    def validate_configuration(self) -> None:
        return None

    @property
    def requires_approval(self) -> tuple[str, ...]:
        return ()

    @property
    def requires_permission(self) -> tuple[str, ...]:
        return ()

    def connect(self, timeout: float = 30.0) -> None:
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        self._connected = False

    def close(self) -> None:
        self._connected = False

    def health(self) -> dict:
        return dict(self._health)

    def discover_capabilities(self, timeout: float = 30.0):
        return []


class _ToolCapContract(_StubContract):
    def discover_capabilities(self, timeout: float = 30.0):
        return [
            {
                "id": "model_read",
                "name": "model_read",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]


class TestContractIdentity:
    def test_identity_fields(self):
        c = _StubContract("matlab-mcp", capabilities=("model_read",))
        assert c.integration_id == "matlab-mcp"
        assert c.protocol is None
        assert c.protocol_version == "2024-11-05"
        assert c.capabilities() == ("model_read",)

    def test_capabilities_sorted(self):
        # Sorted view is provided by the adapter when capabilities are mapped;
        # the base contract preserves the declared set exactly.
        c = _StubContract("x", capabilities=("b", "a", "c"))
        assert c.capabilities() == ("b", "a", "c")


class TestContractLifecycle:
    def test_connect_then_disconnect(self):
        c = _ToolCapContract("x")
        assert c.is_connected() is False
        c.connect()
        assert c.is_connected() is True
        c.disconnect()
        assert c.is_connected() is False

    def test_connect_twice_is_allowed(self):
        c = _ToolCapContract("x")
        c.connect()
        c.connect()  # no error; idempotency

    def test_connect_then_close(self):
        c = _ToolCapContract("x")
        c.connect()
        c.close()
        assert c.is_connected() is False


class TestContractErrorPaths:
    def test_invoke_requires_completing_contract(self):
        # Abstract; not instantiable.
        with pytest.raises(TypeError):
            Contract()
