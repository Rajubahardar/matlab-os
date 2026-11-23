# Phase Dependency Map — Phases 13–28

**Repository:** `E:\matlab os`
**Branch:** `phase11-13/implementation`
**HEAD:** `54efedd`

## Current blockers (before any phase can proceed)

1. **Missing protected files (historical, documented, not currently required to start Phase 13)**
   - `matlab_os/integration/phase4_gateway/gateway.py`
   - `matlab_os/orchestration/core.py`
   - `matlab_os/orchestration/permissions.py`
   - Status: absent from HEAD tree, all reachable refs, and working tree.
   - Action required for full historical integrity: restore from an external source and verify each
     against its exact SHA-256 baseline. Under the current authorization these are treated as documented
     but not required to start Phase 13; they remain separately tracked and have not been deleted,
     fabricated, or reconstructed.
2. **Missing package module — RESOLVED on this branch**
   - `matlab_os.intelligence.engine` referenced by `matlab_os.intelligence.__init__`
   - Status: resolved. A `matlab_os/intelligence/engine.py` now provides the `SharedIntelligence`
     facade so the package imports cleanly. This is the Phase 13 universal model-intelligence facade,
     not a resurrection of the earlier skill-candidate lifecycle class.

## Blocking order

1. `matlab_os.intelligence` importable — **resolved** on this branch.
2. Phase 13 can start — **started** on this branch (in progress).
3. Protected production files — remain a separate historical-integrity item; not currently required
   to proceed with Phase 13 under the current authorization.
4. Later phases depend on earlier ones as listed below.

## Phase dependencies

- Phase 13 — depends on protected files + importable intelligence package.
- Phase 14 — depends on Phase 13; memory if available.
- Phase 15 — depends on MATLAB integration, protected files, approval model.
- Phase 16 — depends on Phase 12 ecosystem context; SQLite memory (`matlab_os.memory.database.DatabaseService`). First working slice on this branch: project-scoped episodic/fact memory facade with provenance, retrieval/ranking, keyed facts, forgetting, and usage counts (`matlab_os.memory.memory`). |
- Phase 17 — depends on Phase 16 for knowledge integration.
- Phase 18 — depends on approval model, audit, permissions.
- Phase 19 — depends on Phase 12 ecosystem and control plane.
- Phase 20 — depends on resource scheduler/governor and actual hardware.
- Phase 21 — depends on Phase 13 routing and model availability.
- Phase 22 — depends on Phase 17 document generation and memory.
- Phase 23 — depends on auth/permission framework.
- Phase 24 — depends on Phases 12–23 back ends.
- Phase 25 — depends on Phases 12–24.
- Phase 26 — depends on earlier phases for real execution paths.
- Phase 27 — depends on Phases 13–26 for measurable workloads.
- Phase 28 — depends on Phases 13–27.

## Shared infrastructure used by many phases

- Control plane (`matlab_os/orchestration/control_plane.py`)
- Agent ecosystem and directory/selector (Phase 9/12)
- Memory (`matlab_os/memory/*`)
- Integration registry (`matlab_os/integration/registry.py`)
- Approval/audit (`matlab_os/phase9/approval*`, `matlab_os/phase9/audit*`)
- Resource governor/scheduler (`matlab_os/phase9/resources/*`, `matlab_os/intelligence/resource_scheduler*`)
- Providers/registry (`matlab_os/phase9/providers/*`)
- Model intelligence (`matlab_os/intelligence/*`)

## Notes

- Reuse existing components rather than recreating registries, routers, memory, or
  orchestration engines.
- Do not assume a discrete NVIDIA GPU.
- Keep automation/execution authority separate from creative and research workflows.
- Protect the four protected production files at every phase boundary.
