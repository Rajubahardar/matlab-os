# Phase 16 — Memory and Knowledge Intelligence (current branch)

## What is present on disk now

- `matlab_os/memory/database.py` — existing SQLite `DatabaseService` (projects,
  experiments, evidence, approvals). Reused as the durable storage layer; not modified
  by this slice.
- `matlab_os/memory/memory.py` — Phase 16 universal memory facade (`MemoryService`)
  built on top of `DatabaseService`. Adds a memory schema and operations:
  - episodic memory (`store_episode`)
  - keyed fact memory (`store_fact`, `get_fact`, `forget_fact`)
  - retrieval with a simple ranking heuristic over recency, relevance, and confidence
    (`retrieve`, `MemoryQuery`, `MemoryQueryBuilder`)
  - explicit forgetting (`forget`, `forget_type`)
  - memory usage/health view (`memory_counts`)
- `matlab_os/memory/__init__.py` — exports `DatabaseService`, `MemoryService`,
  `MemoryRecord`, `MemoryQuery`, `MemoryQueryBuilder`.
- `tests/memory/test_memory.py` — 15 focused tests for the memory facade.

## What is intentionally a different surface

- `matlab_os/integration/agent_ecosystem/memory_facade.py` is an existing,
  more policy-heavy **agent-ecosystem** memory facade. It stores task context as
  experiment/evidence rows, enforces task-scoped permission checks through
  `PermissionManager`, and uses the existing project/experiment/evidence tables.
- The Phase 16 universal memory layer (`MemoryService`) is intentionally simpler and
  different: it is project-scoped episodic/fact memory with ranking and provenance,
  suitable as a general memory surface for later agents/UI. It does **not** replace
  the agent-ecosystem facade.

## What this slice does

- Memory is scoped by project. Operations on a non-existent project id are rejected.
- Every stored memory carries provenance metadata (author, source, created_at,
  updated_at, tags, arbitrary meta).
- Retrieval supports text search over content/key plus filtering by type, tags, and
  minimum confidence, with a small ranking heuristic.
- Keyed facts support an explicit replace rule and do not lower confidence unless
  explicitly passed.
- Forgetting is explicit and auditable by operation (by id, by key, or by type).

## What is intentionally absent (documented, not fabricated)

- No persistent knowledge graph, no embedding-based semantic retrieval, no
  cross-project global memory, no retention policy automation, no conflict resolution
  beyond the simple keyed-fact replace rule. Those are later Phase 16 ground if the
  roadmap continues to require them.
- No new persistence layer: durable storage still lives in `DatabaseService`.

## Tests

- `tests/memory/test_memory.py`: project-scoped episodes/facts, retrieval, ranking,
  keyed facts, forgetting, usage counts, project partitioning, and type checks.

## Dependencies used

- `matlab_os.memory.database.DatabaseService` for durable storage.
- Temporary SQLite databases in tests; no network or external service required.

## Protected production files (unchanged)

The following remain missing and unverifiable from this repo, and are documented as such:

- `matlab_os/integration/phase4_gateway/gateway.py`
- `matlab_os/orchestration/core.py`
- `matlab_os/orchestration/permissions.py`

Their status is unchanged by this slice and remains a separate historical-integrity item.
