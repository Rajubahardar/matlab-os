"""Phase 11 MATLAB transport over ManagedMCPClient.

Adapts the existing :class:`ManagedMCPClient` into the single call
``(tool_name, arguments) -> result`` shape the framework consumes, with the
policy already enforced by the framework layer before it is ever called here.
"""
from __future__ import annotations

from typing import Any, Mapping

from matlab_os.integration.mcp_runtime import ManagedMCPClient


class MATLABTransport:
    """Adapts the live MATLAB MCP client to the framework's transport shape."""

    def __init__(self, client: ManagedMCPClient) -> None:
        self._client = client

    def call(self, tool_name: str, arguments: Mapping[str, Any]) -> Any:
        return self._client.call_tool(tool_name, dict(arguments))
