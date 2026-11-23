"""MATLAB MCP client using the official stdio JSON-RPC transport."""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from matlab_os.memory.database import DatabaseService

logger = logging.getLogger(__name__)


class MCPClient:
    """MCP client for the MathWorks MATLAB MCP server."""

    DEFAULT_SERVER = (
        r"C:\Users\Raju\.matlab\agentic-toolkits\bin"
        r"\matlab-mcp-server.exe"
    )
    DEFAULT_MATLAB_ROOT = r"D:\Apps\Matlab"
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 31515,
        *,
        mcp_server_path: Optional[str] = None,
        cwd: Optional[str] = None,
        matlab_root: Optional[str] = None,
        timeout: int = 30,
        session_mode: str = "new",
        display_mode: str = "nodesktop",
        server_command: Optional[List[str]] = None,
        database: Optional[object] = None,
    ):
        # host and port are retained for compatibility with older callers.
        self.host = host
        self.port = port

        self.mcp_server_path = str(
            Path(mcp_server_path or self.DEFAULT_SERVER)
        )
        self.cwd = str(Path(cwd or Path.cwd()).resolve())
        self.matlab_root = matlab_root or self.DEFAULT_MATLAB_ROOT

        self.timeout = timeout
        self.session_mode = session_mode
        self.display_mode = display_mode
        self.server_command = server_command
        self.database = database

        self.connected = False
        self.tools: List[Dict[str, Any]] = []

        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._request_lock = threading.Lock()
        self._stdout_queue: queue.Queue = queue.Queue()
        self._reader_threads: List[threading.Thread] = []

    def _start_reader_threads(self) -> None:
        """Continuously drain stdout and stderr to prevent pipe blockage."""

        if self._process is None:
            raise RuntimeError("MCP process has not started")

        def read_stdout():
            try:
                for line in self._process.stdout:
                    self._stdout_queue.put(line)
            except Exception as exc:
                logger.debug("MCP stdout reader stopped: %s", exc)
            finally:
                self._stdout_queue.put(None)

        def read_stderr():
            try:
                for line in self._process.stderr:
                    logger.debug("MATLAB MCP stderr: %s", line.rstrip())
            except Exception as exc:
                logger.debug("MCP stderr reader stopped: %s", exc)

        stdout_thread = threading.Thread(
            target=read_stdout,
            name="matlab-mcp-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=read_stderr,
            name="matlab-mcp-stderr",
            daemon=True,
        )

        stdout_thread.start()
        stderr_thread.start()

        self._reader_threads = [stdout_thread, stderr_thread]

    def _send_notification(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send a JSON-RPC notification without expecting a response."""

        if self._process is None or self._process.poll() is not None:
            raise ConnectionError("MCP server process is not running")

        message: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }

        if params is not None:
            message["params"] = params

        payload = json.dumps(message, separators=(",", ":")) + "\n"

        self._process.stdin.write(payload)
        self._process.stdin.flush()

    def _send_rpc_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send a JSON-RPC request and wait for its matching response."""

        with self._request_lock:
            if self._process is None or self._process.poll() is not None:
                raise ConnectionError("MCP server process is not running")

            self._request_id += 1
            request_id = self._request_id

            message: Dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }

            if params is not None:
                message["params"] = params

            payload = json.dumps(message, separators=(",", ":")) + "\n"

            try:
                self._process.stdin.write(payload)
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise ConnectionError(
                    "Could not write to MATLAB MCP server"
                ) from exc

            wait_time = timeout if timeout is not None else self.timeout

            while True:
                try:
                    line = self._stdout_queue.get(timeout=wait_time)
                except queue.Empty as exc:
                    raise TimeoutError(
                        f"MCP request timed out: {method}"
                    ) from exc

                if line is None:
                    raise ConnectionError(
                        "MATLAB MCP server closed stdout"
                    )

                try:
                    response = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "Ignoring malformed MCP response: %s", line.rstrip()
                    )
                    continue

                if not isinstance(response, dict):
                    continue

                if response.get("jsonrpc") != "2.0":
                    continue

                # Ignore server notifications and unrelated messages.
                if response.get("id") != request_id:
                    continue

                if "error" in response:
                    raise RuntimeError(
                        f"MCP error for {method}: {response['error']}"
                    )

                if "result" not in response:
                    raise RuntimeError(
                        f"MCP response for {method} has no result"
                    )

                return response

    def connect(self) -> bool:
        """Start the MCP server and perform the initialization handshake."""

        if self.connected:
            return True

        if not os.path.isfile(self.mcp_server_path):
            logger.error(
                "MATLAB MCP executable not found: %s",
                self.mcp_server_path,
            )
            return False

        if not os.path.isdir(self.cwd):
            logger.error("MCP working directory not found: %s", self.cwd)
            return False

        env = os.environ.copy()

        matlab_bin = os.path.join(self.matlab_root, "bin")
        if os.path.isdir(matlab_bin):
            env["PATH"] = (
                matlab_bin + os.pathsep + env.get("PATH", "")
            )

        args = [
            self.mcp_server_path,
            "--matlab-root", self.matlab_root,
            "--matlab-session-mode", self.session_mode,
            "--matlab-display-mode", self.display_mode,
            "--initial-working-folder", self.cwd,
            "--log-level", "info",
            "--disable-telemetry", "true",
        ]

        try:
            launch_command = self.server_command or args

            self._process = subprocess.Popen(
                launch_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            self._start_reader_threads()

            response = self._send_rpc_request(
                "initialize",
                {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "MATLAB-Expert-Agent",
                        "version": "0.1.0",
                    },
                },
            )

            result = response.get("result", {})
            negotiated_version = result.get("protocolVersion")

            if negotiated_version != self.PROTOCOL_VERSION:
                raise RuntimeError(
                    "Unsupported MCP protocol version returned: "
                    f"{negotiated_version}"
                )

            self._send_notification("notifications/initialized")

            self.connected = True
            logger.info(
                "Initialized MATLAB MCP server: %s",
                result.get("serverInfo", {}),
            )

            return True

        except Exception:
            logger.exception("MATLAB MCP initialization failed")
            self.disconnect()
            return False

    def list_tools(self) -> List[Dict[str, Any]]:
        """Discover tools and their input schemas from the server."""

        if not self.connected:
            raise ConnectionError("Not connected to MATLAB MCP server")

        discovered: List[Dict[str, Any]] = []
        cursor = None

        while True:
            params = {}
            if cursor:
                params["cursor"] = cursor

            response = self._send_rpc_request("tools/list", params)
            result = response["result"]

            discovered.extend(result.get("tools", []))
            cursor = result.get("nextCursor")

            if not cursor:
                break

        self.tools = discovered
        return discovered

    def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        *,
        approved: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Call a discovered MCP tool. Execution is denied by default."""

        if not self.connected:
            raise ConnectionError("Not connected to MATLAB MCP server")

        if not approved:
            raise PermissionError(
                "Tool execution denied: explicit approval is required"
            )

        if not self.tools:
            self.list_tools()

        tool = next(
            (item for item in self.tools if item.get("name") == name),
            None,
        )

        if tool is None:
            raise ValueError(f"Unknown MATLAB MCP tool: {name}")

        response = self._send_rpc_request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
            timeout=timeout,
        )

        result = response["result"]

        if result.get("isError"):
            return {
                "error": True,
                "content": result.get("content", []),
            }

        return {
            "error": False,
            "content": result.get("content", []),
            "structuredContent": result.get("structuredContent"),
        }

    def execute_matlab_code(
        self,
        code: str,
        timeout: int = 60,
        *,
        approved: bool = False,
    ) -> Dict[str, Any]:
        """Execute MATLAB code through the discovered MATLAB tool."""

        if not approved:
            raise PermissionError(
                "MATLAB execution requires explicit approval"
            )

        if not self.tools:
            self.list_tools()

        candidates = (
            "evaluate_matlab_code",
            "execute_matlab_code",
            "execute_code",
        )

        tool = next(
            (
                item for candidate in candidates
                for item in self.tools
                if item.get("name") == candidate
            ),
            None,
        )

        if tool is None:
            raise RuntimeError(
                "No recognized MATLAB code execution tool was discovered. "
                "Inspect list_tools() before selecting a tool."
            )

        schema = tool.get("inputSchema", {})
        properties = schema.get("properties", {})

        code_keys = ("code", "script", "matlab_code")
        code_key = next(
            (key for key in code_keys if key in properties),
            None,
        )

        if code_key is None:
            raise RuntimeError(
                f"Cannot identify code argument for {tool['name']}. "
                f"Schema: {schema}"
            )

        arguments = {code_key: code}

        if "timeout" in properties:
            arguments["timeout"] = timeout

        return self.call_tool(
            tool["name"],
            arguments,
            approved=True,
            timeout=timeout + 10,
        )

    def is_connected(self) -> bool:
        """Return whether the MCP process is still connected."""

        return (
            self.connected
            and self._process is not None
            and self._process.poll() is None
        )

    def disconnect(self) -> None:
        """Close this MCP subprocess without terminating unrelated MATLAB."""

        self.connected = False
        process = self._process
        self._process = None

        if process is None:
            return

        try:
            if process.poll() is None:
                try:
                    process.stdin.close()
                except (OSError, AttributeError):
                    pass

                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "MCP server did not exit after stdin closed; "
                        "terminating its subprocess."
                    )
                    process.terminate()

                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream and not stream.closed:
                        stream.close()
                except OSError:
                    pass

        self.tools = []

    def __enter__(self) -> "MCPClient":
        if not self.connect():
            raise ConnectionError("Could not connect to MATLAB MCP server")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.disconnect()


def run_matlab_code(
    code: str,
    host: str = "localhost",
    port: int = 31515,
    timeout: int = 60,
    *,
    approved: bool = False,
    db: Optional[DatabaseService] = None,
) -> Dict[str, Any]:
    """Run MATLAB code and always clean up the MCP subprocess.

    If a database service is provided, approval is checked via use_approval
    before code execution. This ensures the approval gate (SHA-256 binding,
    15-minute expiry, single-use) is enforced.
    """

    if not approved:
        return {"error": "MATLAB execution requires explicit approval"}

    # Check approval through the database service if provided
    if db is not None:
        if not db.use_approval(approval_id=None, payload=code):  # type: ignore
            return {"error": "Approval invalid, expired, already used, or code has changed."}

    try:
        with MCPClient(host, port, timeout=timeout) as client:
            return client.execute_matlab_code(
                code,
                timeout,
                approved=True,
            )
    except Exception as exc:
        logger.exception("MATLAB execution failed")
        return {"error": str(exc)}

