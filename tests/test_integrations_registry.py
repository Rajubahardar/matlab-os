"""Phase 11 registry consistency and lifecycle tests."""
from __future__ import annotations

import pytest

from matlab_os.integrations.contract import Contract
from matlab_os.integrations.registry import Registry, RegistryError


class _StubContract(Contract):
    def __init__(self, integration_id: str, caps=()):
        self._id = integration_id
        self._caps = tuple(caps)
        self._connected = False

    @property
    def integration_id(self) -> str:
        return self._id

    def description(self) -> str:
        return "stub"

    def capabilities(self) -> tuple[str, ...]:
        return self._caps

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
        return {"status": "ok" if self._connected else "dead"}

    def discover_capabilities(self, timeout: float = 30.0):
        return []


class TestRegistryRegistration:
    def test_register(self):
        reg = Registry()
        c = _StubContract("matlab-mcp", ("model_read",))
        reg.register(c)
        assert reg.all() == ["matlab-mcp"]
        assert reg.lifecycle("matlab-mcp") == "registered"

    def test_duplicate_registration_rejected(self):
        reg = Registry()
        c = _StubContract("matlab-mcp")
        reg.register(c)
        with pytest.raises(Exception):
            reg.register(c)

    def test_get_unknown(self):
        reg = Registry()
        with pytest.raises(KeyError):
            reg.get("missing")

    def test_unregister(self):
        reg = Registry()
        c = _StubContract("matlab-mcp")
        reg.register(c)
        reg.unregister("matlab-mcp")
        assert reg.all() == []


class TestRegistryConfig:
    def test_config_validates(self):
        reg = Registry()
        c = _StubContract("matlab-mcp")
        reg.register(c)
        reg.config("matlab-mcp", {"server": "x"})
        assert reg.availability("matlab-mcp") is False

    def test_config_unknown(self):
        reg = Registry()
        with pytest.raises(KeyError):
            reg.config("missing", {})


class TestRegistryLifecycle:
    def test_initialize(self):
        reg = Registry()
        c = _StubContract("matlab-mcp", ("model_read",))
        reg.register(c)
        reg.initialize("matlab-mcp")
        assert reg.lifecycle("matlab-mcp") == "initialized"
        assert reg.availability("matlab-mcp") is True

    def test_initialize_unknown(self):
        reg = Registry()
        with pytest.raises(KeyError):
            reg.initialize("missing")

    def test_shutdown(self):
        reg = Registry()
        c = _StubContract("matlab-mcp")
        reg.register(c)
        reg.initialize("matlab-mcp")
        reg.shutdown("matlab-mcp")
        assert reg.lifecycle("matlab-mcp") == "registered"

    def test_closed(self):
        reg = Registry()
        reg.close()
        assert reg.registry() == {}
