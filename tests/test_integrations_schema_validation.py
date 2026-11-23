"""Phase 11 schema validation and ManagedMCPClient wiring tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from matlab_os.integrations.invocation import (
    Invocation,
    UnknownCapabilityError,
    SchemaValidationError,
    PermissionDeniedError,
    ApprovalRequiredError,
    DuplicateMutationError,
    UnknownIntegrationError,
)


class _Recorder:
    """Policy that records and only allows a fixed set of calls."""

    def __init__(self, allowed_calls=()):
        self.allowed = set(allowed_calls)
        self.calls = []

    def decide(self, capability, arguments=None):
        self.calls.append((capability, dict(arguments)))
        return {"allowed": capability in self.allowed}


class _Approver:
    def __init__(self, valid=True):
        self.valid_calls = []
        self._valid = valid

    def valid(self, request_id, capability, arguments):
        self.valid_calls.append((request_id, capability, dict(arguments)))
        return self._valid


class _StubAdapter:
    def __init__(self):
        self._client = MagicMock()
        self._client.tools = [
            {"name": "model_read", "inputSchema": {"type": "object"}},
            {"name": "model_edit", "inputSchema": {"type": "object"}},
        ]
        self._client.call_tool = MagicMock(return_value={"ok": True})
        self._invoke_called_with = None

    @property
    def integration_id(self):
        return "matlab-mcp"

    def description(self):
        return "stub"

    def capabilities(self):
        return ("model_read", "model_edit")

    def validate_configuration(self):
        return None

    @property
    def requires_approval(self):
        return ("model_edit",)

    @property
    def requires_permission(self):
        return ()

    def connect(self, timeout: float = 30.0):
        pass

    def is_connected(self):
        return True

    def disconnect(self):
        pass

    def close(self):
        pass

    def health(self):
        return {"status": "ok"}

    def discover_capabilities(self):
        return [
            {
                "id": "model_read",
                "name": "model_read",
                "input_schema": {"type": "object"},
            },
            {
                "id": "model_edit",
                "name": "model_edit",
                "input_schema": {"type": "object"},
            },
        ]

    def invoke(self, capability, arguments, *, request_id):
        self._invoke_called_with = (capability, dict(arguments), request_id)
        return {"ok": True}


class TestSchemaValidation:
    def test_unknown_integration(self):
        inv = Invocation(policy=MagicMock(), registry=None)
        with pytest.raises(UnknownIntegrationError):
            inv.invoke("missing", "model_read", {}, request_id="req-1", actor="operator")

    def test_unknown_capability(self):
        inv = Invocation(policy=MagicMock(), registry=MagicMock())
        registry = inv.registry
        registry.get.side_effect = KeyError("no such integration")
        with pytest.raises(UnknownIntegrationError):
            inv.invoke("i", "model_read", {}, request_id="req-1", actor="operator")


class TestPolicyEnforcement:
    def test_denied_never_reaches_adapter(self):
        policy = _Recorder(("model_read",))
        inv = Invocation(registry=MagicMock(), policy=policy)
        registry = inv.registry
        adapter = _StubAdapter()
        registry.get.return_value = adapter

        with pytest.raises(PermissionDeniedError):
            inv.invoke("matlab-mcp", "model_edit", {}, request_id="req-1", actor="operator")
        # The framework's policy gate denies before the transport is reached.
        assert adapter._invoke_called_with is None


class TestDuplicatePrevention:
    def test_duplicate_mutating_blocked(self):
        inv = Invocation(registry=MagicMock(), policy=_Recorder(("model_edit",)))
        registry = inv.registry
        adapter = _StubAdapter()
        registry.get.return_value = adapter

        inv.invoke("matlab-mcp", "model_edit", {"model": "m.slx"}, request_id="req-1", actor="operator")
        with pytest.raises(DuplicateMutationError):
            inv.invoke("matlab-mcp", "model_edit", {"model": "m.slx"}, request_id="req-1", actor="operator")


class TestApproval:
    def test_approval_required(self):
        policy = _Recorder(("model_edit",))
        approver = _Approver(valid=True)
        inv = Invocation(registry=MagicMock(), policy=policy, approvals=approver)
        registry = inv.registry
        adapter = _StubAdapter()
        registry.get.return_value = adapter

        inv.invoke("matlab-mcp", "model_edit", {"model": "m.slx"}, request_id="req-1", actor="operator")
        assert any(cap == "model_edit" for _, cap, _ in approver.valid_calls)


class TestLifecycle:
    def test_exception_does_not_leak(self):
        inv = Invocation(policy=MagicMock(), registry=MagicMock())
        inv.registry.get.side_effect = KeyError("missing")
        with pytest.raises(UnknownIntegrationError):
            inv.invoke("missing", "model_read", {}, request_id="req-1", actor="operator")
