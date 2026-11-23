"""Mode-aware construction of the MATLAB MCP server command line.

The server's own `--help` contract is the source of truth here:

* ``--matlab-session-mode existing`` refuses ``--matlab-root`` and
  ``--initial-working-folder`` ("not compatible with MATLAB session mode set to
  existing") and then exits immediately.
* ``new`` and ``auto`` accept both, and ``auto`` falls back to starting MATLAB
  when no shared session is found.

The production client hard-codes both flags, so an ``existing`` configuration
dies before the handshake and surfaces as the misleading
``MATLAB MCP server closed stdout``.  This module builds a correct vector and
turns the server's own stderr into a precise diagnostic.

It is additive: the protected client is not modified, and callers opt in by
passing the result through the client's supported ``server_command`` parameter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

VALID_SESSION_MODES = ("new", "existing", "auto")
VALID_DISPLAY_MODES = ("desktop", "nodesktop")
VALID_LOG_LEVELS = ("debug", "info", "warn", "error")

#: Options the server rejects when it must attach to an existing session.
EXISTING_MODE_FORBIDDEN = ("--matlab-root", "--initial-working-folder")


class ServerCommandError(ValueError):
    """Raised when a requested argument combination is invalid."""


class IncompatibleServerArgument(ServerCommandError):
    """Raised when flags contradict the server's own argument contract."""


def build_server_command(
    server_path: str | Path,
    *,
    session_mode: str = "auto",
    matlab_root: str | Path | None = None,
    initial_working_folder: str | Path | None = None,
    extension_files: Iterable[str | Path] = (),
    display_mode: str | None = None,
    log_level: str | None = None,
    log_folder: str | Path | None = None,
    disable_telemetry: bool = True,
    extra_args: Sequence[str] = (),
) -> list[str]:
    """Build a valid argument vector for the MCP server.

    Raises rather than silently dropping a contradictory flag, because silently
    ignoring ``matlab_root`` would leave the operator believing a specific
    MATLAB installation is in use when it is not.
    """

    if session_mode not in VALID_SESSION_MODES:
        raise ServerCommandError(
            f"session_mode must be one of {VALID_SESSION_MODES}, got {session_mode!r}"
        )

    server = str(server_path)
    if not server.strip():
        raise ServerCommandError("server_path is required")

    if session_mode == "existing":
        offending = []
        if matlab_root:
            offending.append("--matlab-root")
        if initial_working_folder:
            offending.append("--initial-working-folder")
        if offending:
            raise IncompatibleServerArgument(
                "MATLAB session mode 'existing' cannot be combined with "
                + ", ".join(offending)
                + ": the server exits with 'option ... is not compatible with "
                "MATLAB session mode set to \"existing\"'. Omit it, or use "
                "session mode 'new'/'auto'."
            )

    args: list[str] = [server, f"--matlab-session-mode={session_mode}"]

    if matlab_root:
        args.append(f"--matlab-root={matlab_root}")
    if initial_working_folder:
        args.append(f"--initial-working-folder={initial_working_folder}")

    for extension in extension_files:
        args.append(f"--extension-file={extension}")

    if display_mode:
        if display_mode not in VALID_DISPLAY_MODES:
            raise ServerCommandError(
                f"display_mode must be one of {VALID_DISPLAY_MODES}"
            )
        args.append(f"--matlab-display-mode={display_mode}")

    if log_level:
        if log_level not in VALID_LOG_LEVELS:
            raise ServerCommandError(
                f"log_level must be one of {VALID_LOG_LEVELS}"
            )
        args.append(f"--log-level={log_level}")

    if log_folder:
        args.append(f"--log-folder={log_folder}")

    if disable_telemetry:
        args.append("--disable-telemetry=true")

    args.extend(extra_args)
    return args


def diagnose_startup_failure(stderr: str, returncode: int | None = None) -> str:
    """Turn server stderr into an actionable message.

    The client otherwise reports only "closed stdout", which hides the real
    cause (an argument contract violation) from the operator.
    """

    text = (stderr or "").strip()
    lowered = text.lower()

    if "not compatible with matlab session mode" in lowered:
        return (
            "MCP server rejected the argument combination: "
            f"{text} "
            "Build the command with build_server_command(session_mode=...) so "
            "existing-session mode omits --matlab-root and "
            "--initial-working-folder."
        )
    if "failed to initialize mcp server" in lowered:
        return (
            "MCP server exited during initialization "
            f"(exit code {returncode}). Server stderr: {text or '<empty>'}. "
            "Check the server log folder for details."
        )
    if not text:
        return (
            "MCP server exited without writing to stderr "
            f"(exit code {returncode}); this usually means invalid arguments or a "
            "missing MATLAB installation."
        )
    return f"MCP server exited (exit code {returncode}): {text}"


__all__ = [
    "build_server_command",
    "diagnose_startup_failure",
    "IncompatibleServerArgument",
    "ServerCommandError",
    "EXISTING_MODE_FORBIDDEN",
    "VALID_SESSION_MODES",
]
