# Status

Last updated: 2026-11-03

## Tests
- tests/test_approvals.py: 7 passed (real pytest, no MATLAB involved).
- Earlier "12 tests passing" claim was wrong: those were scripts, not pytest tests.
- Other pytest tests: none yet except the migration test below.

## Approval gate (matlab_os/memory/database.py)
- Done: payload SHA-256 binding, 15-minute expiry from decision, single-use,
  explicit decision, requester cannot decide own request, no pre-approved creation.
- Known limit: decided_by is only a string; a separate user-run command is still needed.
- Not done: WAL mode, foreign keys, schema_version table.
- Migration of an old-schema approvals table: covered by tests/test_db_migration.py.
  The real data/matlab_agent.db has not been opened yet.

## MATLAB
- Not connected. Nothing run on real MATLAB. All results so far are MOCKED or absent.
- Install root is probably D:\Apps\Matlab (from taskbar shortcut), unverified.
- MCP connector: implemented, NOT verified against real MATLAB.

## Backlog
- mcp_client.py imports `requests` (not installed, not approved): replace with stdlib or get approval.
- test_imports.py in project root fails at collection: move into tests/ or delete.
- get_experiments_by_project reads row[8] for both errors and timestamp.
- Task 3.8 in docs/plan.md: MATLAB session lifecycle.
- Python 3.15.0 in the venv; check pyproject requires-python.
- Original scaffold behaviours (agent init, 10 skills, evidence, project recovery)
  need real pytest tests.

## Next
- Real pytest tests for the scaffold, then user-run approve command, then real MATLAB.
## Findings 2026-11-03 (read before the MATLAB step)
- mcp_client.py talks HTTP to /health and /execute on port 31515. These endpoints are unverified
  and probably do not exist: the SATK server is an MCP (JSON-RPC) server. Do not build on it.
- mcp_client.py never checks an approval before executing; the connector must call
  use_approval(approval_id, code) first.
- model_provider.py has no factory, no mock provider and no "none" default; CloudLLMProvider
  takes api_key as an argument. The approved provider design is not implemented.
- validate_matlab_code is a static pre-check only; real parsing needs MATLAB.
- Agent default db path is relative to the working directory.
