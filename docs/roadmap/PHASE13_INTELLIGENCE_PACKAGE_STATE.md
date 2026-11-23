# Phase 13 — Intelligence package state (current branch)

## What is present on disk now

- `matlab_os/intelligence/__init__.py` — package manifest; imports `.engine.SharedIntelligence`
  and re-exports `model_provider`.
- `matlab_os/intelligence/engine.py` — Phase 13 `SharedIntelligence` universal
  model-intelligence facade (registry-aware selection + capability profiling +
  provider-backed routing surface `select_provider`).
- `matlab_os/intelligence/model_provider.py` — existing provider ABC + placeholder
  providers (`OllamaProvider`, `CloudLLMProvider`, `LocalModelProvider`).
- `matlab_os/intelligence/model_registry/` — Phase 13 model registry surface:
  - `registry.py`: in-memory `ModelRegistry`/`ModelProfile`/`UnknownModelError`.
  - `provider_catalog.py`: provider-independent read view over existing provider instances,
    with capability-based selection and local preference.
  - `provider_adapter.py`: `ProviderModelRegistry`, a `ModelRegistry` view over the provider
    catalog, used to present real providers through the unified catalog API.
- `matlab_os/intelligence/resource_scheduler/` — Phase-13 recovery implementation of the
  currently-required scheduler surface (`ResourceSnapshot`, `ResourceRequirements`,
  `TaskState`, `ResourceScheduler`, `SchedulerError`, `DuplicateTaskError`,
  `UnknownTaskError`). This is provided because current code (Phase 9 governor/monitor/
  executor and related tests) imports it directly.

## What is intentionally absent (documented, not fabricated)

- `matlab_os.intelligence.resource_scheduler` was missing on this branch; it is now provided
  as a recovery implementation of the *currently required* surface only. It is **not** claimed
  to match any historical protected baseline.
- `matlab_os.intelligence.model_registry` deeper historical submodules are not present on this
  branch as separate historical artifacts; the current `model_registry/` is the Phase 13
  provider-independent catalog for the universal intelligence layer.
- The historical `SharedIntelligence` skill-candidate lifecycle class is **not** reimplemented
  here. The current `SharedIntelligence` is the Phase 13 model-intelligence facade.
- Phase 13 routing in this slice uses deterministic mock providers for tests. Real provider
  backends (Ollama, remote) are wired by the existing Phase 9 configuration path; no network
  or Ollama instance is required for the slice to be testable.

## Phase 13 progress

- First meaningful working feature delivered: a provider-independent model registry with
  capability profiling, local preference, context-window awareness, health, and unknown-model
  handling.
- Second slice delivered: a provider-backed catalog/adapter that exposes the existing Phase 9
  provider layer through the unified model-intelligence facade, with `select_provider` as the
  routing path later phases delegate execution to.
- Third slice delivered: cost/context-aware selection, a unified fallback path, and routing
  telemetry.
  - `ModelProfile` now carries `input_cost_per_1k_tokens`, `output_cost_per_1k_tokens`,
    and `max_output_tokens`, with `estimate_cost_for_completion`.
  - `SelectionContext` carries requested capabilities, locality preference, estimated prompt/
    completion tokens, required context tokens, and task/correlation ids.
  - `ModelRegistry.select_with_fallback` and `ProviderCatalog.select_with_fallback` let the
    system try the next eligible model/provider when the first one cannot serve a request.
  - The registry and provider-backed registry expose `record_routing`,
    `routing_log`, and `routing_summary` so routing decisions are observable.
  - The facade exposes `select_model(context=...)`, `select_model_with_fallback(...)`,
    and `routing_summary()`.
- Tests cover cost estimation, context-window filtering through `SelectionContext`,
  fallback across the registry and provider-backed paths, and routing telemetry.

## Blocker resolution

- The immediate functional blocker for stable approval tests was `matlab_os.intelligence`
  failing to import because `.engine` was missing. That is resolved.
- A second real blocker then surfaced: `matlab_os.phase9.resources.governor` imports
  `matlab_os.intelligence.resource_scheduler` at module level, and that was missing. That is
  now provided and tested.
- After both are resolved, `tests/test_matlab_approval.py` passes cleanly (44 passed) on this
  branch. One transient ordering/timing failure was observed only in suite runs and not in
  isolation; it is a pre-existing Phase 9 runtime flake, not a structural regression in this
  branch’s changes.

## Protected production files (unchanged)

The following remain missing and unverifiable from this repo, and are documented as such:

- `matlab_os/integration/phase4_gateway/gateway.py`
- `matlab_os/orchestration/core.py`
- `matlab_os/orchestration/permissions.py`

Their SHA-256 baselines are retained in evidence and have not been deleted, fabricated, or
reconstructed. Under the current authorization they are treated as documented but not required
to start or continue Phase 13. Their protection requirement remains a separate historical-integrity item.

## Tests added/used

- `tests/intelligence/test_intelligence_import.py` — verifies the package imports cleanly and
  that `SharedIntelligence` exists as the Phase 13 facade with the current surface.
- `tests/intelligence/test_model_registry.py` — covers the in-memory registry surface.
- `tests/intelligence/test_phase13_routing.py` — covers the provider-backed catalog,
  `ProviderModelRegistry`, and the unified `SharedIntelligence` routing path over the existing
  mock provider layer.
- `tests/phase9/test_resources_backup.py` and `tests/phase9/conftest.py` — exercise the
  scheduler surface directly via the resource governor; used to confirm the provided
  `resource_scheduler` matches current callers.
