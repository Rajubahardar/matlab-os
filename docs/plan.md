# MATLAB OS Development Plan

## Phase 1: Inspect (Complete)
- [x] Inspect new project directory (E:\matlab os)
- [x] Inscribe existing Cline rules (00-07)
- [x] Inspect legacy project capabilities (D:\localai) without modifying
- [x] Identify reusable persistence and approval logic
- [x] Verify MATLAB installation information (R2024b, Simscape, SATK, MCP server)
- [x] Discover MATLAB MCP tools and schemas

## Phase 2: Design (Complete)
- [x] Project structure: nested packages by function (agent/, integration/, memory/, intelligence/, validation/)
- [x] Storage: SQLite core + file-based large outputs
- [x] Model provider: abstract base + factory, mock + Ollama, default "none"
- [x] Approval gate: SHA-256 binding, single-use, 15min expiry, default deny
- [x] 10 skills documented and registered

## Phase 3: Build
- [ ] Task 3.1: Create pyproject.toml and project scaffolding
  - Files: pyproject.toml, .env.example, .gitignore, config.toml
  - Test: `python -c "import matlab_os; print(' scaffolding OK')"`
- [ ] Task 3.2: Implement MATLAB agent core and 10 skills
  - Files: matlab_os/agent/__init__.py, matlab_agent.py, skills.py
  - Test: Agent initializes with all 10 skills registered
- [ ] Task 3.3: Implement SQLite memory service
  - Files: matlab_os/memory/database.py
  - Test: Store/retrieve projects, experiments, evidence, approvals
- [ ] Task 3.4: Implement MATLAB MCP client adapter
  - Files: matlab_os/integration/mcp_client.py
  - Test: Connect, execute code, handle connection failure (mock mode)
- [ ] Task 3.5: Implement engineering validator
  - Files: matlab_os/validation/engineering_validator.py
  - Test: RMS validation, MATLAB code syntax validation
- [ ] Task 3.6: Implement model provider interface
  - Files: matlab_os/intelligence/model_provider.py
  - Test: Mock provider, factory function, unavailable path
- [ ] Task 3.7: Implement main entry point
  - Files: matlab_os/main.py
  - Test: main() runs without error
- [ ] Task 3.8: MATLAB session lifecycle (later task)
  - If MATLAB is already running (user session), reuse it and NEVER close it.
  - If not running, start MATLAB only after an approval row exists for "start_matlab" (rule 06). One approval covers one work session.
  - Record owned_by_app=true for sessions the app started. Only those are shut down when work finishes, on success/failure/timeout (try/finally), with timeout on shutdown too.
  - Before closing, confirm no unsaved model changes were made by the app, and never run "clear all" or similar on the user workspace.
  - For one-shot scripts such as the RMS demo, prefer "D:\Apps\Matlab\bin\matlab.exe -batch" which exits by itself. Use the MCP session only for interactive Simulink work.
  - MATLAB root is read from config.toml, set to D:\Apps\Matlab pending my check.
  - Tests with a mock process: starts when absent, reuses when present, closes only owned sessions, closes after a failure, never closes a user session.
  - Add a shortcut: comment noting the limit that app-owned detection is based on the process ID it launched.

## Phase 4: Test
- [ ] Task 4.1: Run all automated tests
  - Command: `python -m pytest tests/`
  - Expected: All 12+ tests passing
  - Label each: mocked or real-MATLAB
- [ ] Task 4.2: Verify MCP connectivity
  - Start MATLAB MCP server if available
  - Test read-only health check
  - Test code execution
- [ ] Task 4.3: Execute demonstration task if authorized
  - "Create and execute MATLAB script: 50 Hz sinusoidal, 10 kHz, 0.1s, RMS = 0.707"
  - Validate RMS value
  - Store experiment in SQLite
- [ ] Task 4.4: Measure memory usage
  - Record baseline RAM consumption
  - Verify no background workers

## Phase 5: Report
- [ ] Task 5.1: Provide directory structure
- [ ] Task 5.2: List implemented capabilities
- [ ] Task 5.3: Test results summary
- [ ] Task 5.4: Actual MATLAB integration status
- [ ] Task 5.5: RAM consumption measurement
- [ ] Task 5.6: Storage consumption
- [ ] Task 5.7: Remaining limitations
- [ ] Task 5.8: Next recommended milestone

## Next Milestone
Start MATLAB MCP server and verify connectivity (port 31515), then enable real MATLAB code execution through the adapter.