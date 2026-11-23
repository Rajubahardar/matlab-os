"""Supervised MATLAB MCP client.

Two gaps in the protected :class:`MCPClient` are closed here *without* changing
it:

1. **Argument contract** - the protected client always passes
   ``--matlab-root`` and ``--initial-working-folder``, which the server rejects
   in ``existing`` session mode.  :meth:`ManagedMCPClient.connect` builds a valid
   vector with :func:`build_server_command`.
2. **Process supervision** - the server spawns a separate watchdog process.  The
   protected ``disconnect()`` only terminates the main process, so the watchdog
   survives.  :meth:`ManagedMCPClient.disconnect` kills the whole tree.

Nothing here enables mutation, and no policy decision is made here: authorisation
stays with ``MatlabToolPolicy`` and ``ApprovalAuthority``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from matlab_os.integration.mcp_client import MCPClient
from matlab_os.integration.mcp_command import (
    build_server_command,
    diagnose_startup_failure,
)


def _kill_tree(pid: int) -> None:
    """Terminate a process and its children, without a shell."""

    if pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return
    try:
        os.killpg(os.getpgid(pid), 15)
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, 15)
        except (OSError, ProcessLookupError):
            pass


class ManagedMCPClient(MCPClient):
    """MCP client that builds valid arguments and cleans up its whole tree."""

    def __init__(
        self,
        *,
        server_path: str | Path | None = None,
        session_mode: str = "existing",
        matlab_root: str | Path | None = None,
        initial_working_folder: str | Path | None = None,
        extension_files: Iterable[str | Path] = (),
        log_folder: str | Path | None = None,
        timeout: int = 120,
        **kwargs: Any,
    ) -> None:
        super().__init__(timeout=timeout, **kwargs)
        self.session_mode = session_mode
        self._server_path = str(server_path or self.DEFAULT_SERVER)
        self._matlab_root = matlab_root
        self._initial_working_folder = initial_working_folder
        self._extension_files = tuple(extension_files)
        self._log_folder = log_folder
        self.startup_error: str | None = None

    # -- lifecycle ---------------------------------------------------------- #
    def command(self) -> list[str]:
        return build_server_command(
            self._server_path,
            session_mode=self.session_mode,
            matlab_root=self._matlab_root,
            initial_working_folder=self._initial_working_folder,
            extension_files=self._extension_files,
            log_folder=self._log_folder,
        )

    def connect(self) -> bool:
        self.startup_error = None
        self.server_command = self.command()
        if super().connect():
            # Align streams by clearing transient startup greeting/flush state
            try:
                self.call_tool(
                    "evaluate_matlab_code", {"code": ";"}, approved=True, timeout=5
                )
            except Exception:
                pass
            return True

        stderr = ""
        process = getattr(self, "_process", None)
        if process is not None and process.stderr is not None:
            try:
                stderr = process.stderr.read() or ""
            except (OSError, ValueError):
                stderr = ""
        self.startup_error = diagnose_startup_failure(
            stderr, returncode=getattr(process, "returncode", None)
        )
        return False

    def disconnect(self) -> None:
        process = getattr(self, "_process", None)
        pid = getattr(process, "pid", None)
        super().disconnect()
        if pid:
            # The watchdog is a separate process; the tree kill removes it.
            _kill_tree(int(pid))

    def __enter__(self) -> "ManagedMCPClient":
        if not self.connect():
            raise ConnectionError(
                self.startup_error or "Could not connect to MATLAB MCP server"
            )
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.disconnect()


def server_processes() -> Sequence[int]:
    """PIDs of running ``matlab-mcp-server`` processes (Windows)."""

    if sys.platform != "win32":
        return ()
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process matlab-mcp-server -ErrorAction SilentlyContinue).Id"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        return ()
    return tuple(int(tok) for tok in out.split() if tok.strip().isdigit())


__all__ = ["ManagedMCPClient", "server_processes"]
