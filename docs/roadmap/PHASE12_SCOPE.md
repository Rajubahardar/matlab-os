# Phase 12 — Unified Agent Ecosystem

**Purpose.** Give the Local AI OS one authoritative agent ecosystem contract through which the existing control plane registers, selects, schedules, coordinates, executes, observes and validates specialized agents. The ecosystem is the only path by which work reaches an agent: no second orchestration engine, model router, memory service, permission manager or resource scheduler is created, and existing components are not recreated merely because their APIs need adaptation.

**Scope guard.** Phase 12 deliberately does **not** implement:
- A second orchestration engine.
- A second model router.
- A second memory service.
- A second permission manager.
- A second resource scheduler.
- The Dr. Raju / MATLAB-agent interactive approval loop described in the local filesystem.
- New top-level `matlab_os/agent` classes that duplicate `MATLABAgent`.

Phase 12 reuses those existing components through their documented APIs.

---

## 1. Existing capabilities this phase reuses

| Component | Reuse | Phase 12 role |
| --- | --- | --- |
| `matlab_os.orchestration.control_plane.LocalAIOSControlPlane` | reuse | the only entry point that plans, authorizes and executes work |
| `matlab_os.orchestration.permissions.PermissionManager` | reuse | exact-match, deny-by-default agent/capability grants |
| `matlab_os.phase9.agents.directory.AgentDirectory` | reuse | authoritative lifecycle registry with status transitions |
| `matlab_os.phase9.agents.selector.AgentSelector` | reuse | capability/permission/capacity-based selection with explainable scoring |
| `matlab_os.phase9.runtime.orchestrator.Phase9Runtime` | reuse | orchestration facade, audit, approval gate and execution hooks |
| `matlab_os.phase9.resources.governor.ResourceGovernor` | reuse | admission, concurrency and subprocess ceilings |
| `matlab_os.intelligence.resource_scheduler` | reuse | task state machine, duplicate-task prevention, queue ordering |
| `matlab_os.memory.database.DatabaseService` | reuse | task-scoped context and provenance storage (controlled access, not global) |
| `matlab_os.phase9.agents.capability.AgentCapability` / `AgentProfile` | reuse | typed agent declarations and capability/tool/permission matching |
| `matlab_os.integration.registry.Registry` | reuse | Phase 11 authoritative integration registry |
| `matlab_os.integration.mlab_adapter.MATLABAdapter` | reuse | first integrated agent: the existing stdio MATLAB integration |
| `matlab_os.phase9.approval.ApprovalGate` | reuse | single-use, payload-bound approval tokens |
| `matlab_os.phase9.audit.AuditLog` | reuse | hash-chained audit trail |

---

## 2. Missing capabilities

1. **Unified task contract.** A single typed task/request object that travels from user request through decomposition, assignment and execution. Every event carries the same id spaces.
2. **Agent registry service.** One authoritative registry service that wraps `AgentDirectory`, adds status transitions (`initialized`, `ready`, `running`, `paused`, `failed`, `stopped`), duplicate-ID detection and version-compatibility checks.
3. **Agent lifecycle manager.** A service that owns the lifecycle: `register`, `initialize`, `validate_dependencies`, `ready`, `execute`, `pause`, `resume`, `cancel`, `stop`, `recover_from_failure`.
4. **Capability matcher / selection service.** A thin adapter that consumes `AgentSelector` and exposes the accepted result as a plan-ready assignment decision, including explainable rejection reasons.
5. **Task decomposition service.** Splits one user task into named steps with declared dependencies, budget, owner and authorization scope.
6. **Multi-agent coordination service.** Depends on the Phase 9 `CollaborationCoordinator`, adds dependency-graph validation, explicit result handoff, provenance records and conflict detection.
7. **Memory service facade.** Controlled read/write boundaries around `DatabaseService`: task-scoped context, provenance, no silent overwrites, scope checks.
8. **Model/resource requirement contract.** Agents declare `model`, `context_budget`, `resources` (CPU/RAM/GPU) and `runtime` needs; a validator checks availability before execution.
9. **Cancellation contract.** `cancel` propagates to the resource scheduler and the Phase 9 runtime, clears pending dispatch and prevents new work on the cancelled task.
10. **Security gates.** Explicit checks for permission escalation, unauthorized handoff, unknown agent/capability rejection, and audit completeness.

---

## 3. Architecture boundaries

```
User request
   |
   v
[Phase 12 Agent Ecosystem Service]
   |-- Task validation and decomposition
   |-- Capability matching -> AgentSelector (reused)
   |-- Permission check -> PolicyEngine/PermissionManager (reused)
   |-- Resource admission -> ResourceGovernor (reused)
   |-- Lifecycle registry -> AgentRegistry (new)
   |-- Coordination -> CollaborationCoordinator (reused)
   |-- Verification/aggregation
   v
[Phase 9 Runtime] -> handlers, audit, approval gate
```

The ecosystem service is the **orchestration boundary**, not the execution engine. It never calls a model, touches the file system, starts a process or grants a capability directly. All of those land in the reused Phase 9 components.

---

## 4. Agent contracts

Every registered agent must expose a typed `AgentProfile` with:

- `agent_id`
- `display_name`
- `capabilities` (`AgentCapability` per capability)
- `tools`
- `permission_scope`
- `max_complexity`
- `max_concurrent_tasks`
- `locality`
- `status`
- `enabled`
- `cost_weight`
- `metadata` (redacted on serialisation)

Every task uses the common task contract:

- `task_id` (stable, unique)
- `objective`
- `structured_inputs`
- `context_references`
- `constraints`
- `required_capabilities`
- `timeout` / deadline
- `actor`
- `workload_signature` (hash of immutable input, so replay/duplicate detection is stable)

Every execution result uses the common result contract:

- `task_id`
- `agent_id`
- `status`
- `structured_output`
- `errors`
- `warnings`
- `duration`
- `resource_usage`
- `evidence_refs`
- `verification_status`
- `provenance` (list of upstream agent ids / result ids)

The contract layer rejects malformed tasks and results before they enter the registry.

---

## 5. Lifecycle model

- `initialized` — registered profile, configured, no live session.
- `ready` — dependencies verified; ready to accept dispatch.
- `running` — executing; the resource governor holds a slot.
- `paused` — suspended at the instruction of a controller; can resume.
- `failed` — state can be recovered by clear, bounded recovery.
- `stopped` — final terminal state; no dispatch.
- `cancelling` / `cancelled` — active stop; must prevent new dispatch and allow in-flight cleanup.

Submission state machine: `submitted -> planned -> authorised -> dispatched -> completed | failed | cancelled | timed_out`. A cancellation clears pending dispatch and refuses new step execution.

---

## 6. Permission model

Two independent layers must pass before any dispatched step executes:

1. **Exact-match grant** (`PermissionManager`, deny-by-default).
2. **Declared permission scope** (`AgentProfile`).

A failure raises a typed `PermissionDeniedError` and is audited. Agents cannot elevate their own permissions, grant approvals, or bypass the control plane. Human approval remains mandatory for protected operations; mutations remain disabled by default.

---

## 7. Acceptance criteria

1. One authoritative agent registry exists and owns status transitions.
2. Existing agents use the common contract.
3. Lifecycle management works through `register` to `stopped`.
4. Capability-based selection works and explains every rejection.
5. Scheduling and dependencies work.
6. Multi-agent coordination works.
7. Memory access is controlled.
8. Model and resource checks are enforced.
9. Permission boundaries are tested.
10. Cancellation and failure recovery are demonstrated.
11. A real multi-agent workflow completes successfully.
12. Full regression passes.
13. Documentation accurately describes implemented behavior.

---

## 8. Known constraints

- No real external agent binaries are required. The first integrated agent is the auditable Phase 11 stdio MATLAB adapter, which already exists and carries a real capability registry and real policy/approval behavior.
- Simulation agents (for example a local MATLAB/Simulink build candidate, a power-system test harness, a validation harness) are declared **candidates**, not live agents. A candidate is a plan for implementation and is not accepted as an operational agent unless it passes the common contract and a real execution path.
- `MATLABAgent` and the Dr. Raju interactive approval loop are outside this scope. Their gateway files are deliberately excluded from this commit.
- Model downloads, model replacement and dedicated GPU assumptions are out of scope.
- The control plane, approval surface and audit services in the existing project are required to keep their current security posture.

---

## 9. Status

- `PHASE12_SCOPE` created as the documented plan.
- Existing reusable components inspected.
- Implementation design follows from the inspection above.
- Phase 12 concrete work: agent registry, lifecycle manager, capability matcher, task contract, coordination service, memory facade, model/resource requirement contract, and tests. Then a real multi-agent workflow using the reusable control plane, the existing MATLAB integration and the audit log.
