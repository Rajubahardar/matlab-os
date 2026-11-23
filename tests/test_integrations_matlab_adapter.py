"""Phase 11 MATLAB adapter tests (pure, no MATLAB server required)."""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from matlab_os.integrations.mlab_adapter import (
    MATLABAdapter,
    MATLABAdapterError,
    MATLABAdapterNotReadyError,
    MATLABAdapterUnauthorizedError,
)


class _Policy:
    def __init__(self, allowed=True):
        self.decided = []
        self.allowed = allowed

    def decide(self, capability, arguments=None):
        self.decided.append((capability, dict(arguments)))
        return {"allowed": self.allowed}


class _Approver:
    def __init__(self, valid=True):
        self.valid_calls = []
        self._valid = valid

    def __call__(self, *, capability, arguments, request_id):
        self.valid_calls.append((request_id, capability, dict(arguments)))
        return self._valid


def _client_with_tools(*names):
    client = MagicMock()
    client.tools = [{"name": n, "inputSchema": {"type": "object"}} for n in names]
    client.call_tool = MagicMock(return_value={"ok": True})
    client.connect.return_value = True
    client.connected = True
    return client


class TestAdapterIdentity:
    def test_identity(self):
        client = _client_with_tools("model_read", "evaluate_matlab_code")
        a = MATLABAdapter(server_path="/x")
        a.connect(client=client)
        a.discover_capabilities()
        assert a.integration_id == "matlab-mcp"
        assert a.protocol == "stdio"
        assert "model_read" in a.capabilities()

    def test_invalid_server_path(self):
        a = MATLABAdapter(server_path="/no/such/server.exe")
        with pytest.raises(MATLABAdapterError):
            a.validate_configuration()


class TestAdapterConfiguration:
    def test_existing_mode_rejects_matlab_root(self):
        a = MATLABAdapter(
            server_path="/x",
            matlab_session_mode="existing",
            matlab_root="/foo",
        )
        with pytest.raises(MATLABAdapterError):
            a.validate_configuration()

    def test_configuration_validates(self):
        server = os.environ.get("MATLAB_MCP_SERVER")
        if server and os.path.exists(server):
            a = MATLABAdapter(server_path=server)
            a.validate_configuration()
            return
        pytest.skip("no real matlab-mcp-server binary available for the positive config test")


class TestAdapterDiscover:
    def test_discovers_declared_capabilities(self):
        client = _client_with_tools("model_read", "evaluate_matlab_code")
        a = MATLABAdapter(server_path="/x")
        a.connect(client=client)
        records = list(a.discover_capabilities())
        ids = {r["id"] for r in records}
        assert "model_read" in ids
        assert "evaluate_matlab_code" in ids

    def test_discover_requires_connection(self):
        a = MATLABAdapter(server_path="/x")
        with pytest.raises(MATLABAdapterNotReadyError):
            a.discover_capabilities()


class TestAdapterPolicyEnforcement:
    def test_refuses_denied_tool(self):
        policy = _Policy(allowed=False)
        client = _client_with_tools("model_edit")
        a = MATLABAdapter(server_path="/x", policy=policy)
        a.connect(client=client)
        a._capabilities = ("model_edit",)
        with pytest.raises(MATLABAdapterUnauthorizedError):
            a.invoke("model_edit", {}, request_id="req-1")

    def test_allows_allowed_tool(self):
        policy = _Policy(allowed=True)
        client = _client_with_tools("model_read")
        a = MATLABAdapter(server_path="/x", policy=policy)
        a.connect(client=client)
        a._capabilities = ("model_read",)
        result = a.invoke("model_read", {}, request_id="req-1")
        assert result["ok"] is True


class TestAdapterApproval:
    def test_requires_approval_for_mutations(self):
        approver = _Approver(valid=True)
        client = _client_with_tools("model_edit")
        a = MATLABAdapter(server_path="/x", approve=approver)
        a.connect(client=client)
        a._capabilities = ("model_edit",)
        a.invoke("model_edit", {"model": "m.slx"}, request_id="req-1")
        assert any(cap == "model_edit" for _, cap, _ in approver.valid_calls)


class TestAdapterLifecycle:
    def test_connect_disconnect(self):
        client = _client_with_tools("model_read")
        a = MATLABAdapter(server_path="/x")
        a.connect(client=client)
        assert a.is_connected() is True
        a.disconnect()
        assert a.is_connected() is False

    def test_invoke_without_connection(self):
        a = MATLABAdapter(server_path="/x")
        with pytest.raises(MATLABAdapterNotReadyError):
            a.invoke("model_read", {}, request_id="req-1")
