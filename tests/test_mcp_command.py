"""Task 10.2: mode-aware MCP server command construction.

Includes a live test against the installed R2024b server. The live test is only
skipped when the binary is absent on the machine, so it cannot pass vacuously on
this workstation.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from matlab_os.integration.mcp_client import MCPClient
from matlab_os.integration.mcp_runtime import ManagedMCPClient, server_processes
from matlab_os.integration.mcp_command import (
    IncompatibleServerArgument,
    ServerCommandError,
    build_server_command,
    diagnose_startup_failure,
)

SERVER = Path(r"C:\Users\Raju\.matlab\agentic-toolkits\bin\matlab-mcp-server.exe")
EXTENSION = Path(r"C:/Users/Raju/.matlab/agentic-toolkits/simulink/tools/tools.json")


# -- construction ----------------------------------------------------------- #
def test_existing_mode_omits_incompatible_flags():
    args = build_server_command(
        SERVER,
        session_mode="existing",
        extension_files=[EXTENSION],
    )
    assert "--matlab-session-mode=existing" in args
    assert not any(arg.startswith("--matlab-root") for arg in args)
    assert not any(arg.startswith("--initial-working-folder") for arg in args)
    assert f"--extension-file={EXTENSION}" in args
    assert "--disable-telemetry=true" in args


def test_new_mode_keeps_matlab_root_and_folder():
    args = build_server_command(
        SERVER,
        session_mode="new",
        matlab_root=r"D:\Apps\Matlab",
        initial_working_folder=r"E:\matlab os",
    )
    assert "--matlab-root=D:\\Apps\\Matlab" in args
    assert "--initial-working-folder=E:\\matlab os" in args


def test_auto_mode_keeps_both():
    args = build_server_command(
        SERVER, session_mode="auto", matlab_root=r"D:\Apps\Matlab"
    )
    assert "--matlab-root=D:\\Apps\\Matlab" in args


@pytest.mark.parametrize("flag", ["matlab_root", "initial_working_folder"])
def test_existing_mode_rejects_incompatible_flag(flag):
    with pytest.raises(IncompatibleServerArgument, match="existing"):
        build_server_command(SERVER, session_mode="existing", **{flag: "x"})


def test_multiple_extension_files_are_repeatable():
    args = build_server_command(
        SERVER, session_mode="existing", extension_files=["a.json", "b.json"]
    )
    assert args.count("--extension-file=a.json") == 1
    assert args.count("--extension-file=b.json") == 1


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"session_mode": "bogus"}, "session_mode"),
        ({"session_mode": "new", "display_mode": "headless"}, "display_mode"),
        ({"session_mode": "new", "log_level": "verbose"}, "log_level"),
    ],
)
def test_invalid_options_are_rejected(kwargs, message):
    with pytest.raises(ServerCommandError, match=message):
        build_server_command(SERVER, **kwargs)


def test_empty_server_path_is_rejected():
    with pytest.raises(ServerCommandError):
        build_server_command("   ")


# -- diagnostics ------------------------------------------------------------ #
def test_session_mode_conflict_is_explained():
    message = diagnose_startup_failure(
        'Error with supplied arguments: option "initial-working-folder" is not '
        'compatible with MATLAB session mode set to "existing".',
        returncode=1,
    )
    assert "build_server_command" in message
    assert "not compatible" in message


def test_generic_failure_mentions_exit_code():
    assert "exit code 3" in diagnose_startup_failure("boom", returncode=3)


def test_silent_failure_is_not_reported_as_success():
    message = diagnose_startup_failure("", returncode=2)
    assert "exited" in message
    assert "without writing to stderr" in message


# -- live server ------------------------------------------------------------ #
@pytest.mark.skipif(not SERVER.is_file(), reason="MATLAB MCP server not installed")
def test_existing_mode_connects_to_the_real_server():
    command = build_server_command(
        SERVER, session_mode="existing", extension_files=[EXTENSION]
    )
    client = MCPClient(server_command=command, timeout=120)
    assert client.connect() is True
    try:
        tools = client.list_tools()
        names = {tool.get("name") for tool in tools}
        assert len(names) >= 14
        assert {"model_overview", "evaluate_matlab_code"} <= names
    finally:
        client.disconnect()
    assert client.is_connected() is False


@pytest.mark.skipif(not SERVER.is_file(), reason="MATLAB MCP server not installed")
def test_incompatible_combination_fails_before_handshake():
    """The server itself must reject the old vector, proving the defect."""
    args = [
        str(SERVER),
        "--matlab-session-mode=existing",
        r"--matlab-root=D:\Apps\Matlab",
    ]
    proc = subprocess.run(
        args,
        input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
        capture_output=True,
        text=True,
        timeout=120,
    )
    combined = " ".join(((proc.stdout or "") + (proc.stderr or "")).lower().split())
    assert "not compatible with matlab session mode" in combined


@pytest.mark.skipif(not SERVER.is_file(), reason="MATLAB MCP server not installed")
def test_managed_client_leaves_no_server_processes():
    before = set(server_processes())
    client = ManagedMCPClient(
        server_path=SERVER, session_mode="existing", extension_files=[EXTENSION]
    )
    assert client.connect() is True, client.startup_error
    try:
        assert len(client.list_tools()) >= 14
    finally:
        client.disconnect()

    import time

    for _ in range(20):
        if set(server_processes()) <= before:
            break
        time.sleep(0.25)
    leaked = set(server_processes()) - before
    assert leaked == set(), f"leaked MCP server processes: {leaked}"



def test_managed_client_refuses_incompatible_vector_before_launch():
    # Stronger than a runtime message: the bad vector is never built.
    client = ManagedMCPClient(
        server_path=SERVER,
        session_mode="existing",
        matlab_root=r"D:\Apps\Matlab",
    )
    with pytest.raises(IncompatibleServerArgument):
        client.command()
    assert client.startup_error is None


def test_managed_client_reports_missing_server_binary():
    missing = SERVER.parent / "definitely-not-installed.exe"
    client = ManagedMCPClient(server_path=missing, session_mode="existing")
    assert client.connect() is False
    assert client.startup_error
    assert "exited" in client.startup_error
