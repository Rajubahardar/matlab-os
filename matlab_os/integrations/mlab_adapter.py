"""MATLAB (stdios MCP) integration for the Phase 11 framework.

This is the reference protocol adapter: it wraps the existing
:class:`ManagedMCPClient` and adapts it to the common integration contract.
It does **not** create a second control path and it does not duplicate the
operation classification or approval logic already defined in
``matlab_os.integration.matlab_policy`` and ``matlab_os.integration.matlab_approval``.

The adapter is intentionally narrow: it supports the real local MATLAB MCP
server through ``ManagedMCPClient`` and nothing else. Unsupported protocols are
rejected at configuration time rather than stubbed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from matlab_os.integration.mcp_command import build_server_command
from matlab_os.integration.mcp_runtime import ManagedMCPClient

from matlab_os.integrations.contract import Contract, CapabilityRecord


class MATLABAdapterError(RuntimeError):
    """Base error for the MATLAB integration."""


class MATLABAdapterNotReadyError(MATLABAdapterError):
    """The connection is not usable."""


class MATLABAdapterUnauthorizedError(MATLABAdapterError):
    """An operation was refused by policy."""


class MATLABAdapter(Contract):
    """Integration contract implementation over the real MATLAB MCP server."""

    #: Capability ids we will accept. Anything else is rejected upstream.
    ACCEPTED_CAPABILITIES = (
        "detect_matlab_toolboxes",
        "model_overview",
        "model_read",
        "model_scan",
        "model_read_diagnostics",
        "model_edit",
        "model_test",
        "run_matlab_file",
        "run_matlab_test_file",
        "model_query_params",
        "model_resolve_params",
        "model_check",
        "evaluate_matlab_code",
        "check_matlab_code",
    )

    def __init__(
        self,
        *,
        server_path: str | Path | None = None,
        matlab_root: str | Path | None = None,
        matlab_session_mode: str = "existing",
        initial_working_folder: str | Path | None = None,
        extension_files=(),
        log_folder: str | Path | None = None,
        timeout: int = 120,
        policy: Any | None = None,
        approve: Any | None = None,
        audit: Any | None = None,
    ) -> None:
        self._name = "MATLAB MCP Server"
        self._version = "1.0"
        self._protocol = "stdio"
        self._protocol_version = "2024-11-05"
        self._description = "Real MATLAB / Simulink MCP server (stdios)."
        self._policy = policy
        self._approve = approve
        self._audit = audit

        self._server_path = str(server_path or self._default_server())
        self._session_mode = matlab_session_mode
        self._matlab_root = matlab_root
        self._initial_working_folder = initial_working_folder
        self._extension_files = tuple(extension_files)
        self._log_folder = log_folder
        self._timeout = timeout

        self._client: ManagedMCPClient | None = None
        self._capabilities: tuple[str, ...] = ()

    # -- identity ---------------------------------------------------------- #

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    @property
    def protocol(self) -> str:
        return self._protocol

    @property
    def protocol_version(self) -> str:
        return self._protocol_version

    @property
    def description(self) -> str:
        return self._description

    @property
    def integration_id(self) -> str:
        return "matlab-mcp"

    def capabilities(self) -> tuple[str, ...]:
        return self._capabilities

    # -- configuration --------------------------------------------------- #

    def validate_configuration(self) -> None:
        """Reject unsupported configurations before anything connects."""
        if not self._server_path or not Path(self._server_path).exists():
            raise MATLABAdapterError(
                f"MATLAB MCP server binary not found: {self._server_path}"
            )
        if self._session_mode not in ("new", "existing", "auto"):
            raise MATLABAdapterError(
                f"Unsupported session mode: {self._session_mode!r}"
            )
        # The MATLAB client rejects --matlab-root / --initial-working-folder in
        # existing session mode; refuse silently truncated configurations.
        if self._session_mode == "existing":
            if self._matlab_root is not None or self._initial_working_folder is not None:
                raise MATLABAdapterError(
                    "session mode 'existing' cannot combine matlab_root "
                    "or initial_working_folder; use 'new' or 'auto'."
                )

    @property
    def requires_approval(self) -> tuple[str, ...]:
        # Mutations are approved by the separate approval surface and by the
        # policy. The framework consults both before invoking; this attribute
        # only lists which capabilities are sensitive.
        return (
            "model_edit",
            "model_test",
            "run_matlab_file",
            "run_matlab_test_file",
            "evaluate_matlab_code",
        )

    @property
    def requires_permission(self) -> tuple[str, ...]:
        # Default posture: only the framework's permission gate is authoritative.
        return ()

    # -- lifecycle ------------------------------------------------------- #

    def connect(self, timeout: float = 30.0, client: ManagedMCPClient | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            self._client = ManagedMCPClient(
                server_path=self._server_path,
                session_mode=self._session_mode,
                matlab_root=self._matlab_root,
                initial_working_folder=self._initial_working_folder,
                extension_files=self._extension_files,
                log_folder=self._log_folder,
                timeout=int(timeout),
            )
        if not self._client.connect():
            raise MATLABAdapterNotReadyError(self._client.startup_error or "connect failed")

    def is_connected(self) -> bool:
        return self._client is not None and self._client.connected

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.disconnect()
            self._client = None

    def close(self) -> None:
        self.disconnect()

    def health(self) -> dict[str, Any]:
        """Live health snapshot. Never raises: failures become ``unhealthy``."""
        if self._client is None:
            return {"status": "not_connected", "protocol": self._protocol}
        try:
            return {
                "status": "connected" if self._client.connected else "not_ready",
                "protocol": self._protocol,
                "protocol_version": self._protocol_version,
                "server_path": self._server_path,
            }
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc)}

    # -- capability discovery -------------------------------------------- #

    def discover_capabilities(self, timeout: float = 30.0) -> Iterable[CapabilityRecord]:
        """Return validated capability records from the live server.

        The server's own tool description is the source of truth; nothing here
        is trusted, everything is validated against the integration contract.
        """
        if self._client is None:
            raise MATLABAdapterNotReadyError("not connected")
        tools = self._client.tools
        records = []
        for tool in tools:
            tid = self._normalise_tool_id(tool.get("name", ""))
            if tid not in self.ACCEPTED_CAPABILITIES:
                # Known tools the framework refuses by default are not silently
                # exposed as capabilities. They are still available through the
                # direct transport, but never indexed here.
                continue
            schema = self._normalise_schema(tool.get("inputSchema") or tool.get("input_schema"))
            records.append(
                {
                    "id": tid,
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "input_schema": schema,
                    "protocol_version": self.protocol_version,
                    "source": "matlab-mcp-server",
                }
            )
        self._capabilities = tuple(sorted({r["id"] for r in records}))
        return records

    # -- invocation ------------------------------------------------------ #

    def invoke(self, capability: str, arguments: Mapping[str, Any], *, request_id: str) -> Any:
        if self._client is None:
            raise MATLABAdapterNotReadyError("not connected")
        if capability not in self._capabilities:
            raise MATLABAdapterError(
                f"Capability {capability!r} is not provided by this integration."
            )

        # Policy is the decision point; the transport is only called after the
        # framework has authorised the operation. The adapter never grants.
        decision = self._policy.decide(capability, arguments) if self._policy else None
        if decision is not None and not decision.get("allowed", True):
            raise MATLABAdapterUnauthorizedError(decision.get("reason", "not allowed"))

        if capability in self.requires_approval:
            self._approve(
                capability=capability,
                arguments=dict(arguments),
                request_id=request_id,
            )

        result = self._client.call_tool(capability, dict(arguments))
        return self._normalize_result(result)

    # -- helpers --------------------------------------------------------- #

    def _default_server(self) -> str:
        return (
            r"C:\Users\Raju\.matlab\agentic-toolkits\bin"
            r"\\matlab-mcp-server.exe"
        )

    @staticmethod
    def _normalise_tool_id(name: str) -> str:
        # MCP tools may carry namespaces that we keep only when defined by the
        # server; unknown prefixes are collapsed to the bare id.
        if name.startswith("matlab/"):
            return name.split("/", 1)[1]
        return name

    @staticmethod
    def _normalise_schema(schema: Any) -> dict[str, Any]:
        if not isinstance(schema, Mapping):
            return {}
        # The server sometimes returns a JSON Schema map; normalise the shape
        # we depend on.
        result: dict[str, Any] = {}
        for key, value in schema.items():
            if isinstance(value, Mapping):
                result[key] = dict(value)
            else:
                result[key] = value
        return result

    def _normalize_result(self, result: Any) -> dict[str, Any]:
        # Normalise MCP content payloads into a stable shape for the audit path.
        if isinstance(result, Mapping):
            return dict(result)
        if isinstance(result, str):
            return {"text": result}
        return {"value": result}
