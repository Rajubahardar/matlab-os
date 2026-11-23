# Phase 11 — Universal Integration Framework

**Purpose.** Give the Local AI OS control plane one secure, extensible and
protocol-independent integration layer through which it discovers,
manages, validates and invokes external tools and services. Every integration
goes through the same contract, the same registry, the same policy and the same
audit pipeline, so a capability exists in exactly one place and no second
implementation can silently shadow the first.

Phase 11 covers the integration layer only. It deliberately stops short of the
agent orchestration, model routing and resource scheduling already defined by
Phase 9; those systems consume the integration layer through the registry.

## Existing components this phase reuses

Phase 11 is built on top of, and never duplicates:

- **`ManagedMCPClient`** (`matlab_os/integration/mcp_runtime.py`) — the
  mode-aware stdio MATLAB client.
- **`MatlabToolPolicy`** (`matlab_os/integration/matlab_policy.py`) — the
  operation classification and permission gate. Read-only by default.
- **Existing MCP command builder** (`matlab_os/integration/mcp_command.py`) —
  mode-aware, conflict-raising argument vector.
- **Existing approval surface** (`matlab_os/integration/approval_surface.py`,
  `approval_cli.py`, `ApprovalAuthority`) — human, single-use, persisted
  approvals.
- **Phase 9 control plane** (`LocalAIOSControlPlane`,
  `matlab_os/orchestration/control_plane.py`) — policy and audit authority.
- **Existing audit facilities** (`matlab_os/phase9/audit.py`) — hash-chained,
  redacting audit log.
- **Existing resource scheduler** (`matlab_os/intelligence/resource_scheduler.py`)
  — resource admission and scheduling.
- **Existing internal Python integrations** — `matlab_os/integration/` package.

Where a Phase 9 component already owns a registry (for example
`AgentRegistry` in `matlab_os/orchestration/core.py` or
`ProviderRegistry` in `matlab_os/phase9/providers/registry.py`), Phase 11
extends that component with an integration-aware contract instead of creating a
second registry. The Phase 11 registry is the single authoritative place for
integration identity, configuration, capability indexing and lifecycle state.

## Required architecture

### A. Integration contract

Every integration implements one common interface:

- Integration identity (id, name, version, protocol).
- Declared capabilities (tool ids and what they do).
- Configuration validation.
- Permission requirements.
- Initialization / connection / disconnect / cleanup.
- Capability discovery.
- Health reporting.
- Invocation.

### B. Integration registry

A single authoritative registry holds:

- Registration and unregistration.
- Lookup by integration id and by capability.
- Configuration validation.
- Capability indexing.
- Availability state (initialized, connected, healthy, degraded, closed).
- Lifecycle state.

### C. Protocol adapter layer

- The existing MATLAB STDIO MCP implementation is the first and reference
  adapter.
- The adapter contract is extensible for future protocols.
- No unsupported protocol is implemented merely for architectural completeness.

### D. Capability discovery

- Discovered tool metadata and schemas are treated as untrusted data, never as
  executable instructions.
- The framework normalizes identifiers, input schemas and capability names.
- Validation covers:
  - tool identifier format;
  - input schema validity;
  - required arguments;
  - duplicate capabilities;
  - unknown or malformed fields;
  - capability changes between connections.

### E. Controlled invocation

Every tool call passes through one policy-enforced path:

`Request -> Resolve integration -> Resolve capability -> Validate schema ->
Check permissions -> Check approval requirements -> Resource admission ->
Invoke -> Normalize result -> Audit`

- Policy checks happen **before** any transport invocation.
- Agents never reach an integration transport directly.

### F. Lifecycle and reliability

Implemented:

- Connection lifecycle with explicit states.
- Timeouts on connect/read/write.
- Cancellation where supported.
- Process supervision, including tree cleanup.
- Clean disconnect.
- Error normalization.
- Safe retry rules (no automatic retry of mutating operations unless
  idempotency is explicitly established).
- Duplicate mutation prevention.
- Failure recovery.

### G. Monitoring and audit

Recorded per call:

- Integration id.
- Capability / tool id.
- Request id.
- Actor.
- Permission decision.
- Approval decision where applicable.
- Start / end timestamps and duration.
- Result status.
- Error category.

Sensitive material (secrets, signing keys, raw argument values) is never logged.

## Initial supported integration: MATLAB MCP (stdios)

MATLAB MCP is the reference implementation. The framework must integrate the
existing MATLAB tool policy and approval surface without weakening either.

- Read-only MATLAB tools require no approval and may run under the default
  policy.
- `evaluate_matlab_code` remains classified as **code execution**.
- Arbitrary MATLAB code execution stays disabled unless separately authorized.
- Mutations require an explicit `MatlabToolPolicy` grant **and** a valid,
  unexpired, unconsumed human approval ticket.
- Approval tickets are bound to tool, canonical arguments, normalised target,
  actor and request id.

## Requirements

- Deny by default. Unknown tools and unknown integrations are refused.
- Invalid schemas are rejected.
- No secret is exposed to agents or external providers.
- No public listener, tunnel or network exposure beyond an MCP server.
- No uncontrolled process spawning.
- No duplicate mutation execution.
- The full regression suite must pass.
