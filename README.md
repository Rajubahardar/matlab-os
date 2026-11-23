# MATLAB OS - Local AI OS V1

Private repository for a local agent that does MATLAB and Simulink engineering work.
It drives the MATLAB installation on this machine through the Simulink Agentic Toolkit
MCP server, keeps project memory in SQLite, and asks for approval before it executes
code.

**Status:** 1.0.0rc1 - release candidate, feature-frozen. Details in [STATUS.md](STATUS.md).

---

## Recovering after a crash

On a fresh laptop, in PowerShell, two lines:

~~~powershell
git clone https://github.com/Rajubahardar/matlab-os.git; cd matlab-os
python -m pip install -e .; Copy-Item config.example.toml config.toml
~~~

Then point it at this machine's MATLAB and start it:

~~~powershell
Get-Content config.toml | Select-String 'engine_path'
Test-Path "D:/Apps/Matlab/bin/matlab.exe"
.\start.ps1
~~~

Git Bash equivalent of the two lines:

~~~bash
git clone https://github.com/Rajubahardar/matlab-os.git && cd matlab-os && python -m pip install -e . && cp config.example.toml config.toml
~~~

Three things worth knowing before you start:

- **The clone asks you to sign in to GitHub.** The repository is private, so the first
  clone opens a browser sign-in or asks for a personal access token. That is normal.
- **Check `engine_path` in `config.toml`.** If MATLAB is not at
  `D:/Apps/Matlab/bin/matlab.exe` on the new machine, change that one line. A standard
  install is `C:/Program Files/MATLAB/R2024b/bin/matlab.exe`.
- **Git brings back the code, not MATLAB.** MATLAB R2024b, Simulink and a licence must
  be installed on the machine that runs the agent. The two lines restore the project;
  they cannot install MathWorks software.

---

## Requirements

| Requirement | Notes |
|---|---|
| Windows | PowerShell 5.1 or newer |
| Python 3.11+ | `python -V` to check |
| MATLAB R2024b + Simulink | licensed and installed locally - the agent calls it |
| Simulink Agentic Toolkit | provides `matlab-mcp-server.exe` |

## Setup on a machine

~~~powershell
cd "E:\matlab os"
python -m pip install -e .
Copy-Item config.example.toml config.toml     # only if config.toml does not exist yet
.\start.ps1
~~~

`pip install -e .` installs the `matlab-os` package and the `v1-cli` command. Add
`,engine` inside the brackets only if you also need the MATLAB Engine API for Python;
the launcher talks to MATLAB over HTTP, so it is usually not required.

## Checking the machine

~~~powershell
python -V                                                  # 3.11 or newer
Get-Content config.toml | Select-String 'engine_path'      # is the path right?
Test-NetConnection localhost -Port 31515                   # only after start.ps1 has run
Invoke-WebRequest http://localhost:31515/health -UseBasicParsing
~~~

The last two are expected to fail before the server is up. That is not an error.

## Running it

~~~powershell
.\start.ps1
~~~

1. Reads `host` and `port` from `config.toml` (default `localhost:31515`).
2. Checks `http://<host>:<port>/health`.
3. Starts `matlab-mcp-server.exe` only if the health check fails.
4. Opens the interface in your default browser.
5. Stops that server again on Ctrl+C. A server that was already running is left alone.

Git Bash users can run `./start.sh` instead.

## Command line

~~~powershell
v1-cli
~~~

The task workflow is submit, status, retrieve, history. `python -m matlab_os.cli` is the
module form.

## Configuration

`config.toml` is git-ignored on purpose - it holds machine-specific paths. The
recoverable copy lives in `config.example.toml`.

| Key | Meaning |
|---|---|
| `[mcp] host`, `port` | where the MCP connector listens; default `localhost:31515` |
| `[mcp] engine_path` | path to `matlab.exe` |
| `[mcp] mcp_server_path` | optional override for `matlab-mcp-server.exe` |
| `[database] path` | SQLite file for project memory |
| `[approval] require_approval` | must stay `true`; code does not run without a decision |
| `[approval] timeout_seconds` | how long an approval request stays valid |
| `[logging] level`, `file` | log verbosity and location |

## Troubleshooting

**"MCP server not found"** - set `mcp_server_path` in `config.toml` to the real location:

~~~toml
[mcp]
mcp_server_path = "C:/Users/<you>/.matlab/agentic-toolkits/bin/matlab-mcp-server.exe"
~~~

**"MCP server exited immediately"** - the binary may speak stdio only and has to be
started by your MCP client. Start it that way; `start.ps1` will attach through the
health check instead.

**"did not answer ... within 60 seconds"** - MATLAB or the connector is not up:

~~~powershell
Test-NetConnection localhost -Port 31515
Invoke-WebRequest http://localhost:31515/health -UseBasicParsing
~~~

**`v1-cli` is not recognised** - the package is not installed in the environment you are
using. Re-run `python -m pip install -e .` in that same terminal.

## What git does not back up

- **`config.toml`** - machine paths. Recreated from `config.example.toml`.
- **`data/`** - SQLite project memory: projects, experiments, evidence, approvals.
  Copy `data/matlab_agent.db` to the new machine if that history matters to you.
- **`logs/`** - only useful for the run that produced them.
- **MATLAB and its licence** - per machine, outside the repository.

## Repository layout

~~~
matlab-os/                     (= E:\matlab os)
  README.md  STATUS.md
  pyproject.toml  config.example.toml  .gitignore
  start.ps1  start.sh
  discover_matlab.py  test_runner_check.py
  run_live_candidate_validation.py  run_skill16_live_validation.py
  run_tests.bat
  matlab_os/                   the agent package
  tests/                       test suite
  docs/                        engineering documents
  frontend/                    web interface
  .clinerules/                 engineering rules for AI assistants
  data/  logs/                 runtime, git-ignored
~~~

## Versioning

Semantic versioning, one goal per version, tagged releases. `main` always holds
something that runs; V2 is developed on its own branch so V1 stays usable on its tag.

## Known failures at release

These two are documented and expected - they are not evidence of a broken install:

1. `test_ollama_provider_constructs_with_reused_adapter` - no live Ollama service at
   the default endpoint.
2. `test_registry_from_default_config_never_builds_disabled_deepseek` - Ollama is
   enabled but the registry does not resolve it as the active provider.

Both are post-V1 fixes.
