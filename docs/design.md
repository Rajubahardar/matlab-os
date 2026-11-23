# MATLAB OS Design Documentation

## Agent Overview
- **Name**: MATLAB Expert Agent
- **Version**: 0.1.0
- **Purpose**: Senior-level MATLAB and Simulink engineering agent
- **Scope**: One agent with ten skills, no additional agents

## Sixteen Skills
1. MATLAB Programming and Debugging
2. Simulink Model Development
3. Power Systems Engineering
4. Control Systems Engineering
5. Simscape Electrical
6. Simulation and Analysis
7. Optimization and Parameter Tuning
8. Engineering Verification and Validation
9. Research Paper Reproduction
10. MATLAB Project Management and Documentation
11. Electrical Machines Engineering
12. Power Electronics Engineering
13. Renewable Energy and Microgrids
14. Power Quality and Harmonics
15. Engineering Data Acquisition and Processing
16. MATLAB Function Intelligence

The canonical registry is `matlab_os/agent/skills.py`; every other document must
agree with it.

## Storage Architecture
- **Core persistence**: SQLite database (`data/matlab_agent.db`)
  - Tables: projects, experiments, evidence, approvals
- **Large outputs**: File-based under `data/artifacts/<experiment_id>/`
  - Artifact metadata in SQLite: path, size, SHA-256, created_at, experiment_id
  - Format: .mat, .csv, .png, .json (small only)
- **Schema versioning**: schema_version table for migrations
- **No pickle**: stdlib sqlite3 only, no serialization packages
- **WAL mode**: Enabled for concurrent read/write performance

## Model Provider Interface
- **Abstract base**: single method `generate(prompt, **options) -> str` + `is_available() -> bool`
- **Factory function**: reads `config.toml` / environment variables to pick provider
- **Default provider**: "none" (agent works without any model)
- **Implemented providers**:
  - Mock provider for tests (labelled MOCK)
  - Ollama over plain HTTP using standard library, no new packages
- **Cloud provider**: interface only, no implementation until explicitly requested
- **API keys**: environment variables only, never logged or stored in SQLite
- **Never**: download/pull models automatically
- **Tests**: factory, mock, and unavailable-provider path

## Approval Management
- **Table**: `approvals` with columns:
  - id, operation, target, payload_sha256, requested_at, status
  - decided_at, decided_by, used_at, experiment_id
- **SHA-256 binding**: approval tied to exact script/action hash
  - One character change invalidates the approval
- **Single-use**: status becomes "used" after execution
- **Default expiry**: 15 minutes
- **Default deny**: no matching approved row means no execution
- **Interactive only**: approve/reject from user prompt or CLI command
- **No self-approval**: agent code must never approve its own request
- **Universal gate**: every execution, overwrite, and deletion checks the gate
- **No bypass flags**: no "trusted" mode

## Project Structure
```
matlab-os/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── config.toml
├── docs/
│   ├── design.md (this file)
│   └── plan.md (next tasks)
├── matlab_os/
│   ├── __init__.py
│   ├── main.py
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── matlab_agent.py
│   │   └── skills.py
│   ├── integration/
│   │   ├── matlab_connector.py
│   │   └── mcp_client.py
│   ├── memory/
│   │   ├── database.py
│   │   └── experiments.py
│   ├── intelligence/
│   │   └── model_provider.py
│   └── validation/
│       └── engineering_validator.py
├── projects/
├── data/
├── logs/
├── tests/
└── docs/
```

## MATLAB Integration
- **MCP reuse**: MATLAB MCP server on port 31515 (C:\Users\Raju\.matlab\agentic-toolkits\bin\matlab-mcp-server.exe)
- **Connector adapter**: Lightweight client, not a competing server
- **Engine API fallback**: MATLAB Engine API when MCP unavailable
- **Execution support**: timeouts, structured logging, error handling,
  controlled working directories, permission checks, execution status,
  reproducible experiment records
- **Read-only connectivity**: Verify before claiming success

## Persistence Design
- **A**: SQLite for all persistence (projects, experiments, evidence, approvals)
- **B**: SQLite for core data + files for large simulation outputs
- **Rejected**: C (pickle, no database)

## Key Design Principles
- Lazy senior developer: best code is code never written
- First-principles optimization: minimize RAM, CPU, disk
- Engineering integrity: never fabricate results, distinguish generated from tested
- Resource priorities: RAM efficiency > storage efficiency > reliability > correctness > speed
- Do not claim success without test evidence