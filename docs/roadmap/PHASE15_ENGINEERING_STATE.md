# Phase 15 — MATLAB / Simulink Engineering Intelligence (current branch)

## What is present on disk now

- `matlab_os/engineering/__init__.py` — Phase 15 engineering layer:
  - `EngineeringError`, `ContractValidationError`, `VerificationError`
  - `ExecutionTarget`: a describe-only descriptor of where a MATLAB/Simulink
    operation is meant to run (workspace root, task id, tool, target mapping,
    target kind).
  - `TaskContract`: a constrained, versioned engineering task contract describing
    objective, operation, tool, target, output expectation, required capabilities,
    and workspace root.
  - `SimulationResult`: the outcome of one simulation/analysis run.
  - `SimulationResultVerified`: a result that has passed/failed explicit checks.
  - `SimulationVerifier`: verifies results against expectations using structure,
    success, and output-kind checks and returns a verdict with explicit check
    lists.
  - `EngineeringMemory`: a bridge that records engineering operations and
    simulation results as memories through the existing `MemoryService`.
- `tests/engineering/test_engineering_intelligence.py` — 23 focused tests for
  the contract model, verifier, and engineering-memory bridge.

## What this slice is

- A thin **describe-and-verify** engineering layer. It can model what a MATLAB/
  Simulink operation should do, describe where it should run, record whether a
  result passed its engineering checks, and persist that history through the
  existing memory system.
- It is intentionally not an executor. It does not connect to MATLAB, launch
  simulations, or run the MATLAB MCP server by itself.

## What real MATLAB/Simulink work still depends on

- The existing control plane and MCP transport paths in
  `matlab_os/integration/` and `matlab_os/orchestration/control_plane.py`.
- Approval policy and audit from the existing Phase 9/15 integration surfaces.
- The existing MATLAB MCP client (`matlab_os/integration/mcp_client.py`) when the
  system is wired for it and the user has approvals in place.

## Relationship to other layers

- Reuses the existing `MemoryService` from Phase 16 for durability and provenance;
  it does **not** introduce a second memory store.
- Task contracts are the engineering view that later planning, approval, and
  execution paths can share: they name what the operation is, what it may touch,
  and what output it must produce.
- Simulation verification is the engineering “does the result look right?” gate,
  not a full scientific validation pass.

## Tests

- Contract validation and execution-target construction.
- Snapshot round-tripping and contract-to-execution-target mapping.
- Simulation-result construction and success semantics.
- Simulation verifier: minimal passing result, tool-reported failure, and
  numeric/mapping/list/output-key expectations.
- Verification logging and counts.
- Engineering-memory bridge: recording operations and verified/failed results.

## What is intentionally absent (documented, not fabricated)

- No execution engine, no Simulink model builder, no signal-extraction or
  domain-specific scientific validation, no scheduling of simulations, no
  automated regression harness.
- No new persistence layer.

## Protected production files (unchanged)

The following remain missing and unverifiable from this repo, and are documented
as such:

- `matlab_os/integration/phase4_gateway/gateway.py`
- `matlab_os/orchestration/core.py`
- `matlab_os/orchestration/permissions.py`

Their status is unchanged by this slice and remains a separate historical-
integrity item.
