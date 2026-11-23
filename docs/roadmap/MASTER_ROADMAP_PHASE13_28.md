# Master Roadmap — Phases 13–28

**Repository:** `E:\matlab os`
**Branch:** `phase11-13/implementation`
**HEAD:** `a3eb816`
**Programme entry status on this branch:** `BLOCKED` at the historical protected-file level is still documented as a separate item, but the functional import blocker for `matlab_os.intelligence` is resolved on this branch and Phase 13 has started; Phase 15 and Phase 16 have each delivered a first working slice here.

This roadmap is the controlling plan for the autonomous Phases 13–28 programme. It
is intentionally concise. Full details live in the per-phase evidence directories
under `staging/phaseNN_*` and in the completion matrix
`docs/roadmap/PHASE_COMPLETION_MATRIX.md`.

---

## Programme entry gate — protected files and import blocker

**Current status on this branch:**

- `matlab_os/integration/mcp_client.py` — OK (`2c914408b0f33f143e34ebd01b7fad3abef7db08a444d6cc559297383d1d48d2`)
- `matlab_os/integration/phase4_gateway/gateway.py` — **MISSING**
- `matlab_os/orchestration/core.py` — **MISSING**
- `matlab_os/orchestration/permissions.py` — **MISSING**

There is no exact-match source for the three missing protected files in this repo
(history, reachable refs, checkpoints, stash, or working tree). Earlier Phase 10
and Phase 12 evidence records those SHA-256 values as attestations but does not
contain the file bytes.

Therefore the protected-file item remains a separate historical-integrity item. It
is documented, retained in evidence, and **not** deleted, fabricated, or
reconstructed. Under the current authorization it is treated as documented but not
currently required to start or continue Phase 13.

### Functional import blocker for `matlab_os.intelligence` — resolved on this branch

In addition to the protected-file item above, Phase 13 was previously blocked from
start because `matlab_os.intelligence.__init__` imports `.engine.SharedIntelligence`,
but `matlab_os/intelligence/engine.py` was absent from disk and from the current
git HEAD tree. That import blocker is now resolved: the Phase 13
`SharedIntelligence` universal model-intelligence facade is present, and
`matlab_os.intelligence` imports cleanly on this branch.

### Approval-suite stability on this host

`tests/test_matlab_approval.py` imports and runs cleanly on this branch
(44 passed). One transient suite-ordering/timing flake was observed earlier only
in full-suite runs and not in isolation; on this branch it was addressed with an
optional monitor seam plus a deterministic monitor in the control-plane approval
tests, without weakening the production governor. Approval tests are stable on this
host after that fix.

### What has been delivered since programme start

- Phase 13 has started and delivered three working slices here:
  1. a provider-independent model registry with capability profiling, local
     preference, context-window awareness, health, and unknown-model handling;
  2. a provider-backed catalog/adapter that exposes the existing Phase 9 provider
     layer through the unified `SharedIntelligence` facade, with
     `select_provider` as the routing path later phases delegate execution to;
  3. cost/context-aware selection, a unified fallback path, and routing telemetry.
- Phase 15 has started and delivered a first working slice here: MATLAB/Simulink
  task contracts, execution targets, simulation-result verification, and an
  engineering-memory bridge through the existing memory surface.
- Phase 16 has started and delivered a first working slice here: a unified
  project-scoped memory facade (`MemoryService`) on top of the existing
  `DatabaseService`, with episodic/fact memory, provenance, retrieval/ranking,
  keyed facts, forgetting, and memory usage counts.

The roadmap below is still useful: it defines the target and the dependencies, and
it tells us where the protective boundary is.
## Master roadmap, phases 13–28

### Phase 13 — Universal Model Intelligence
**Status:** `IN PROGRESS` on this branch (3 slices delivered)
**Objective:** One observable, provider-independent model intelligence layer with
registry, discovery, capability profiling, local/remote providers, Ollama,
context-window and cost awareness, hardware-aware routing, fallback, health,
and lifecycle.
**Reuse:** existing resource scheduler, agent ecosystem, control plane, model
intelligence components, existing provider registry.
**Slices delivered on this branch:**
1. provider-independent model registry with capability profiling, local preference,
   context-window awareness, health, and unknown-model handling;
2. provider-backed catalog/adapter exposing the existing Phase 9 provider layer
   through the unified `SharedIntelligence` facade, with `select_provider` as the
   routing path later phases delegate execution to;
3. cost/context-aware selection, a unified fallback path, and routing telemetry
   (`ModelProfile` cost fields, `SelectionContext`,
   `select_with_fallback` on registry/catalog/adapter/facade, `record_routing`,
   `routing_log`, `routing_summary`, `estimate_cost_for_completion`).
**Files likely touched:** `matlab_os/intelligence/*`,
`matlab_os/orchestration/*`, `matlab_os/phase9/providers/*`,
`matlab_os/integration/*`.
**Dependencies:** protected files present (documented separate item), `matlab_os.intelligence` imports cleanly.
**Security:** provider/plugin inputs treated as untrusted; no agent self-approval;
no direct transport access from agents.
**Testing:** routing selects an appropriate model; fallback activates on failure;
cost/context telemetry observable; provider failure does not crash the system.
**Acceptance:** multiple agents can request capabilities and receive an appropriate
model through a unified, observable routing path.
**Evidence:** `staging/phase13_universal_model_intelligence_<timestamp>/`
**Limitations:** none accepted yet; real model utilization depends on configured
local providers. On this branch, routing is verified with deterministic mock
providers; real provider backends are wired by the existing Phase 9 config path.

### Phase 14 — Scientific Research Intelligence
**Status:** `NOT STARTED` (blocked on Phase 13 + protected files)
**Objective:** Research workflow: task decomposition, paper discovery/metadata,
literature review, citation/source tracking, gap identification, comparison/ranking,
reproducibility analysis, research memory, report generation, LaTeX/IEEE support,
simulation analysis, source-grounded claims.
**Reuse:** agent ecosystem, memory, document/text handling if available, control plane.
**New work:** research planner, paper/metadata ingestion, citation graph, evidence
records, report templating.
**Files likely touched:** new research package; likely reuse of memory and document
modules later.
**Dependencies:** Phase 13 model selection/routing; memory; document handling.
**Security:** source data treated as untrusted; citations preserved; no fabricated
references; claims must be traceable to sources.
**Testing:** end-to-end research trace from query to cited report with evidence refs.
**Acceptance:** a complete research workflow discovers, analyzes, preserves evidence,
identifies gaps, and produces a traceable report.
**Evidence:** `staging/phase14_scientific_research_intelligence_<timestamp>/`

### Phase 15 — Engineering Intelligence
**Status:** `IN PROGRESS` on this branch (first slice delivered)
**Objective:** Engineering-domain agent environment: MATLAB/Simulink workflows,
power/control EE analysis, numerical simulation, equation/unit handling, parameter
validation, result verification, documentation, reproducibility, provenance.
**Reuse:** MATLAB MCP integration, control plane, agents, validators, memory.
**Slice delivered on this branch:** an engineering layer focused on describe-and-verify
work: engineering task contracts, execution targets, simulation-result verification,
and an engineering-memory bridge that records operations and verified/failed results
through the existing memory surface. This slice does not connect to or execute MATLAB
by itself; real MATLAB/Simulink work still flows through the control plane / MCP
client when the system is wired for it and approvals are in place.
**Files likely touched:** `matlab_os/integration/*`, engineering/validation modules,
documentation.
**Dependencies:** MATLAB integration; protected files; approval model.
**Security:** execution authority does not blur with engineering correctness;
real execution evidence required; no “error: false means correct”.
**Testing:** engineering task planned, executed, validated against declared criteria,
reported with reproducible evidence.
**Acceptance:** as above.
**Evidence:** `staging/phase15_engineering_intelligence_<timestamp>/`

### Phase 16 — Memory and Knowledge Intelligence
**Status:** `IN PROGRESS` on this branch (first slice delivered)
**Objective:** Unified long-term memory: structured persistent memory, project-level
context, agent-specific context, shared knowledge, retrieval/ranking, provenance,
versioning, conflict resolution, duplicate detection, extraction, retention/deletion,
backup/recovery, access control.
**Reuse:** SQLite `DatabaseService`, existing memory components.
**Slice delivered on this branch:** a unified project-scoped memory facade
(`MemoryService`) on top of the existing `DatabaseService`, with episodic/fact
memory, provenance, retrieval/ranking, keyed facts with a no-lower-confidence
replace rule, explicit forgetting, and memory usage counts. A separate,
more policy-heavy agent-ecosystem `MemoryFacade` already existed in
`matlab_os/integration/agent_ecosystem/memory_facade.py` and is intentionally
preserved; Phase 16 universal memory is a different, simpler surface.
**Files likely touched:** `matlab_os/memory/*`.
**Dependencies:** Phase 12 ecosystem for access control context; protected files.
**Security:** scope checks, no silent overwrites, access control, backup/restore.
**Testing:** persist/retrieve/update/share with correct project isolation and provenance.
**Acceptance:** as above.
**Evidence:** `staging/phase16_memory_knowledge_intelligence_<timestamp>/`

### Phase 17 — Document and Information Intelligence
**Status:** `NOT STARTED`
**Objective:** PDF/DOCX/XLSX/PPTX/text processing, OCR where appropriate, indexing,
search/retrieval, structured extraction, table extraction, summarization,
comparison, knowledge-base integration, citation/source preservation, document
generation, version-aware file management.
**Reuse:** file/memory modules when ready.
**New work:** document ingestion pipeline, extraction, indexing, generation.
**Files likely touched:** document ingestion modules, knowledge/memory integration.
**Dependencies:** Phase 16 for knowledge integration.
**Security:** file parsing is untrusted input; preserve source integrity; isolate
processing.
**Testing:** ingest, index, retrieve, analyze, generate while preserving sources and
file integrity.
**Acceptance:** as above.
**Evidence:** `staging/phase17_document_information_intelligence_<timestamp>/`

### Phase 18 — Browser and Desktop Automation
**Status:** `NOT STARTED`
**Objective:** Browser/desktop automation with workflow execution, UI observation,
action verification, screenshot/state evidence, recovery from navigation failures,
permission-aware execution, human approval for sensitive actions, credential
protection.
**Reuse:** approval model, audit, permission framework.
**New work:** automation adapter, verification/recovery, approval gates for sensitive
actions.
**Files likely touched:** new automation adapter; approval/audit integration.
**Dependencies:** approval model; Phase 15/25 orchestration for workflow integration.
**Security:** must not bypass auth, access control, or consent; protect sessions
and credentials.
**Testing:** authorized tasks executed through supported interfaces with verifiable
actions and recoverable failure states.
**Acceptance:** as above.
**Evidence:** `staging/phase18_browser_desktop_automation_<timestamp>/`

### Phase 19 — Autonomous Workflow Engine
**Status:** `NOT STARTED`
**Objective:** Multi-step workflow engine: planning, dependency-aware scheduling,
task decomposition, agent assignment, execution state, retry classification,
checkpointing, recovery, cancellation, approval gates, history, audit trails,
failure propagation, result validation.
**Reuse:** Phase 12 ecosystem, control plane, approval gate, audit.
**New work:** workflow state machine, checkpoint/recovery, retry classification,
approval gating.
**Files likely touched:** orchestration + new workflow modules.
**Dependencies:** Phase 12; Phase 13/15/16 as consumers.
**Security:** workflow transitions under permission/audit; no silent privilege
escalation; failure propagation contained.
**Testing:** multi-agent workflow executes dependent tasks, preserves state, recovers
from eligible failures, reports traceable result.
**Acceptance:** as above.
**Evidence:** `staging/phase19_autonomous_workflow_engine_<timestamp>/`

### Phase 20 — Distributed Computing and Resource Intelligence
**Status:** `NOT STARTED`
**Objective:** Local/remote execution abstraction, distributed task scheduling,
hardware-aware placement, CPU/RAM/GPU/disk monitoring, resource budgets,
thermal-aware scheduling where measurable, queue management, parallel execution,
isolation, reclamation, monitoring, recovery.
**Reuse:** resource scheduler, governor, monitoring.
**New work:** remote execution abstraction, placement policies, queue/budget controls.
**Files likely touched:** resource scheduler/ governor modules.
**Dependencies:** actual hardware baseline; not assuming discrete NVIDIA GPU.
**Security:** isolation, no uncontrolled resource use, no overload.
**Testing:** workloads scheduled by available resources without uncontrolled memory
or overload.
**Acceptance:** as above.
**Evidence:** `staging/phase20_distributed_resource_intelligence_<timestamp>/`

### Phase 21 — Voice, Vision and Multimodal Intelligence
**Status:** `NOT STARTED`
**Objective:** Speech-to-text, text-to-speech, voice-command interpretation, image
understanding, multimodal routing, context preservation, permission-aware actions,
model capability routing, local-first where practical.
**Reuse:** model routing (Phase 13), modality adapters.
**New work:** modality adapters, routing, context preservation.
**Files likely touched:** multimodal adapters, routing integration.
**Dependencies:** Phase 13 routing; model availability.
**Security:** permission-aware; protect audio/visual data and sessions.
**Testing:** users interact through supported modalities with routing and security
controls.
**Acceptance:** as above.
**Evidence:** `staging/phase21_voice_vision_multimodal_<timestamp>/`

### Phase 22 — Creative Intelligence
**Status:** `NOT STARTED`
**Objective:** Creative agent integration, image-generation workflow, content/
presentation/visual design assistance, creative memory, asset management, revision
workflows, output validation; separated from engineering/execution authority.
**Reuse:** creative agents, memory, document generation.
**New work:** creative workflow, asset/revision management, validation.
**Files likely touched:** creative modules; document/asset integration.
**Dependencies:** Phase 17 document generation; memory.
**Security:** creative generation separate from execution authority.
**Testing:** creative tasks completed through specialized agents/workflows without
compromising other functions.
**Acceptance:** as above.
**Evidence:** `staging/phase22_creative_intelligence_<timestamp>/`

### Phase 23 — Productivity and External Ecosystem
**Status:** `NOT STARTED`
**Objective:** Email/calendar integrations where authorized, file-system workflows,
project management integrations, external service connectors, plugin/connector
registry, auth management, permission scopes, sync, error handling, integration
health monitoring.
**Reuse:** external connector model, permission scopes, audit.
**New work:** connector registry, authorization/scoping, health monitoring.
**Files likely touched:** external integration modules.
**Dependencies:** auth/permission framework.
**Security:** least-privilege, supported APIs, credential protection.
**Testing:** external services connected through consistent permission-aware interfaces.
**Acceptance:** as above.
**Evidence:** `staging/phase23_productivity_external_<timestamp>/`

### Phase 24 — Unified User Interface
**Status:** `NOT STARTED`
**Objective:** Unified chat UI, agent/model selection and visibility, task dashboard,
workflow monitoring, approval interface, resource monitoring, memory/project
management, file interaction, logs, error visibility, settings, accessibility,
responsive layout; UI must communicate what the system is doing/accesses/requires
approval for.
**Reuse:** all back-end phases.
**New work:** main UI shell and dashboards.
**Files likely touched:** new UI layer, integration with back-end services.
**Dependencies:** Phases 12–23 back-end capabilities.
**Security:** UI never grants authority on its own; approval remains separate.
**Testing:** main capabilities accessible through one coherent interface.
**Acceptance:** as above.
**Evidence:** `staging/phase24_unified_ui_<timestamp>/`

### Phase 25 — Full-System Orchestration
**Status:** `NOT STARTED`
**Objective:** Unified orchestration across all implemented agents: cross-domain
planning, collaboration, shared context, model selection, resource-aware scheduling,
workflow state, dependency management, approval enforcement, result aggregation,
failure recovery, auditability.
**Reuse:** all back-end phases.
**New work:** cross-domain orchestration/coordination.
**Files likely touched:** orchestration facade, integration adapters.
**Dependencies:** Phases 12–24.
**Security:** request travels through correct agents/models/tools/workflows/validators
without bypassing policies.
**Testing:** end-to-end cross-domain request with enforced policies and audit.
**Acceptance:** as above.
**Evidence:** `staging/phase25_full_system_orchestration_<timestamp>/`

### Phase 26 — Security and Reliability
**Status:** `NOT STARTED`
**Objective:** Threat modeling, authn/authz, least privilege, agent isolation, tool
permissions, human approval, credential/secret protection, path validation, command
execution controls, network exposure controls, dependency security, audit logging,
data integrity, backup/restore, recovery testing, failure containment, security
regression tests. Review Phase 8–12 findings.
**Reuse:** approval/audit, permission framework, protection baselines.
**New work:** security regression suite, recovery tests, threat documentation.
**Files likely touched:** security modules across system.
**Dependencies:** earlier phases for actual execution paths to test.
**Security:** critical phase; do not declare complete on static/unit success alone.
**Testing:** actual execution paths including unauthorized, malformed, interrupted,
adversarial requests.
**Acceptance:** as above.
**Evidence:** `staging/phase26_security_reliability_<timestamp>/`

### Phase 27 — Performance and Optimization
**Status:** `NOT STARTED`
**Objective:** Startup/inference/agent latency, memory/CPU/GPU/disk usage, thermal
behavior, model load/unload, caching, scheduling optimization, concurrency limits,
bottleneck analysis, regression tests. Do not weaken security/correctness.
**Reuse:** monitoring, scheduler.
**New work:** benchmarks, caching/optimization, regression.
**Files likely touched:** performance-sensitive modules.
**Dependencies:** Phases 13–26 capabilities measured.
**Security:** no optimization that weakens security/correctness.
**Testing:** measured against documented workloads and real hardware limits.
**Acceptance:** as above.
**Evidence:** `staging/phase27_performance_optimization_<timestamp>/`

### Phase 28 — Integration Validation and Benchmarking
**Status:** `NOT STARTED`
**Objective:** Evaluate the entire Local AI OS: end-to-end, cross-agent, cross-model,
integration, real tool execution, MATLAB validation, workflow recovery, security
regression, performance benchmarks, resource stress, persistence, installation,
backup/restore, long-running stability, failure injection, documentation
verification, known limitation register. Use recording transports only for isolated
unit tests; critical production workflows also need real integration evidence.
**Reuse:** everything.
**New work:** final validation suites and limitation register.
**Files likely touched:** whole system.
**Dependencies:** Phases 13–27.
**Security:** include security regression and failure injection.
**Testing:** pass documented end-to-end criteria; remaining risks explicitly recorded.
**Acceptance:** as above.
**Evidence:** `staging/phase28_integration_validation_<timestamp>/`

After Phase 28: produce
`docs/production/LOCAL_AI_OS_PRODUCTION_READINESS_REPORT.md` and assign final
assessment: `NOT READY FOR PRODUCTION`, `CONDITIONALLY READY`, or
`READY FOR CONTROLLED PRODUCTION RELEASE`.

Phase 29 is the controlled production release phase and requires separate user
authorization.

---

## Shared dependency map (short)

1. Protected production files present and verified — **still a separate
   documented item on this branch**:
   `matlab_os/integration/phase4_gateway/gateway.py`,
   `matlab_os/orchestration/core.py`, and
   `matlab_os/orchestration/permissions.py` are absent from the repo and
   unverifiable here. They have not been deleted, fabricated, or reconstructed.
2. `matlab_os.intelligence` imports cleanly — **resolved on this branch**.
3. Phase 13 routing/registry available — **started on this branch**; 3 slices
   delivered.
4. Phases 14–17 depend on Phase 13 plus memory/documents as they exist.
5. Phase 15 has started on this branch with a first working describe-and-verify
   engineering slice; it still depends on MATLAB integration, protected files, and
   the approval model for real execution.
6. Phase 16 has started on this branch with a first working unified memory slice;
   it depends on the existing SQLite `DatabaseService` and the Phase 12 ecosystem
   context for access control.
7. Phase 18+ depend on approval/audit/permissions.
8. Phase 19 workflow engine depends on Phase 12 ecosystem.
9. Phase 25 depends on Phases 12–24.
10. Phase 26 depends on real execution paths from earlier phases.
11. Phase 27 depends on Phases 13–26 for measurable workloads.
12. Phase 28 depends on Phases 13–27.

---

## Method notes

- Reuse before rebuild.
- Vertical slice early for each phase.
- Commit only coherent, tested work.
- Never weaken security/correctness to make a phase pass.
- Protect the four production files at every phase boundary.
- Record evidence from actual execution, not from mock-only success claims.
- Report the exact verification level for external integrations.
