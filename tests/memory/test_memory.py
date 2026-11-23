"""Phase 16 unified memory tests.

These tests cover the memory facade built on top of the existing ``DatabaseService``:
project-scoped episodes and facts, retrieval with basic ranking, keyed facts,
forgetting, and memory usage counts.
"""

from __future__ import annotations

import pathlib

import pytest

from matlab_os.memory import (
    DatabaseService,
    MemoryQuery,
    MemoryQueryBuilder,
    MemoryRecord,
    MemoryService,
)


@pytest.fixture
def tmp_db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "memory.db"


@pytest.fixture
def memory(tmp_db_path: pathlib.Path) -> MemoryService:
    db = DatabaseService(db_path=str(tmp_db_path))
    mem = MemoryService(db)
    pid = db.store_project("demo", "objectives")
    mem._project_id = pid  # small convenience for tests
    yield mem
    mem._db.close()


@pytest.fixture
def pid(memory: MemoryService) -> int:
    # Read the project id from the database directly.
    row = memory._db.conn.cursor().execute(
        "SELECT id FROM projects ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    return int(row[0])


def test_store_and_retrieve_episode(memory: MemoryService, pid: int) -> None:
    mid = memory.store_episode(
        pid,
        "Simulink check failed on step 3.",
        author="agent",
        source="simulink_mcp",
        confidence=0.8,
        tags={"matlab", "simulink"},
    )
    records = memory.retrieve(
        MemoryQuery(project_id=pid, text="Simulink check")
    )
    assert len(records) == 1
    assert records[0].memory_id == mid
    assert records[0].memory_type == "episode"
    assert records[0].author == "agent"
    assert set(records[0].tags) == {"matlab", "simulink"}


def test_store_and_retrieve_fact(memory: MemoryService, pid: int) -> None:
    mid = memory.store_fact(
        pid,
        key="simulink.last_check",
        content="failed at step 3",
        source="simulink_mcp",
        confidence=0.9,
    )
    fact = memory.get_fact(pid, "simulink.last_check")
    assert fact is not None
    assert fact.memory_id == mid
    assert fact.memory_type == "fact"
    assert fact.key == "simulink.last_check"
    assert fact.content == "failed at step 3"
    assert fact.confidence == pytest.approx(0.9)


def test_fact_replacement_preserves_id(memory: MemoryService, pid: int) -> None:
    mid = memory.store_fact(
        pid,
        key="plan.status",
        content="pending",
        confidence=0.5,
    )
    memory.store_fact(
        pid,
        key="plan.status",
        content="in progress",
        confidence=0.7,
        replace=True,
    )
    updated = memory.get_fact(pid, "plan.status")
    assert updated is not None
    assert updated.memory_id == mid
    assert updated.content == "in progress"
    assert updated.confidence == pytest.approx(0.7)


def test_fact_replace_false_keeps_existing(memory: MemoryService, pid: int) -> None:
    memory.store_fact(pid, key="x", content="old", confidence=0.9)
    mid = memory.store_fact(
        pid, key="x", content="new", confidence=0.2, replace=False
    )
    kept = memory.get_fact(pid, "x")
    assert kept is not None
    assert kept.memory_id == mid
    assert kept.content == "old"
    assert kept.confidence == pytest.approx(0.9)


def test_retrieval_ranks_recent_and_confident(memory: MemoryService, pid: int) -> None:
    old_id = memory.store_episode(
        pid,
        "Old observation from last week.",
        confidence=0.9,
        tags={"old"},
    )
    recent_id = memory.store_episode(
        pid,
        "Recent confident observation.",
        confidence=0.9,
        tags={"recent"},
    )
    memory._db.conn.cursor().execute(
        "UPDATE memory SET updated_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (old_id,),
    )
    memory._db.conn.commit()

    results = memory.retrieve(
        MemoryQuery(project_id=pid, memory_type="episode", limit=10)
    )
    assert results[0].memory_id == recent_id


def test_retrieval_filters_by_type_and_tags(memory: MemoryService, pid: int) -> None:
    memory.store_episode(pid, "alpha", tags={"matlab"})
    memory.store_episode(pid, "beta", tags={"simulink"})
    memory.store_episode(pid, "gamma", tags={"matlab", "simulink"})

    matlab_only = memory.retrieve(
        MemoryQuery(project_id=pid, tags={"matlab"})
    )
    content = {r.content for r in matlab_only}
    assert content == {"alpha", "gamma"}


def test_retrieval_filters_by_min_confidence(memory: MemoryService, pid: int) -> None:
    memory.store_episode(pid, "low confidence note", confidence=0.2)
    memory.store_episode(pid, "high confidence note", confidence=0.9)

    high = memory.retrieve(
        MemoryQuery(project_id=pid, min_confidence=0.5)
    )
    assert {r.content for r in high} == {"high confidence note"}


def test_forget_fact(memory: MemoryService, pid: int) -> None:
    memory.store_fact(pid, key="temp", content="remove me")
    assert memory.forget_fact(pid, "temp") is True
    assert memory.get_fact(pid, "temp") is None


def test_forget_memory_by_id(memory: MemoryService, pid: int) -> None:
    mid = memory.store_episode(pid, "forget me")
    assert memory.forget(pid, mid) is True
    assert memory.retrieve(MemoryQuery(project_id=pid, text="forget me")) == []


def test_forget_type(memory: MemoryService, pid: int) -> None:
    memory.store_episode(pid, "e1")
    memory.store_episode(pid, "e2")
    removed = memory.forget_type(pid, "episode")
    assert removed == 2
    assert memory.list_memories(pid, memory_type="episode") == []


def test_memory_counts(memory: MemoryService, pid: int) -> None:
    memory.store_episode(pid, "e")
    memory.store_fact(pid, key="f1", content="c1")
    memory.store_fact(pid, key="f2", content="c2")
    counts = memory.memory_counts(pid)
    assert counts["total"] == 3
    assert counts["by_type"] == {"episode": 1, "fact": 2}


def test_memory_is_partitioned_by_project(tmp_db_path: pathlib.Path) -> None:
    """Memory written to one project must not appear in another project's queries."""
    db = DatabaseService(db_path=str(tmp_db_path))
    mem = MemoryService(db)

    pid_a = db.store_project("proj-a", "objectives for a")
    pid_b = db.store_project("proj-b", "objectives for b")

    mem.store_episode(pid_a, "only in a", tags={"a"})
    mem.store_episode(pid_b, "only in b", tags={"b"})

    a_only = mem.retrieve(MemoryQuery(project_id=pid_a, tags={"a"}))
    b_only = mem.retrieve(MemoryQuery(project_id=pid_b, tags={"b"}))

    assert {r.content for r in a_only} == {"only in a"}
    assert {r.content for r in b_only} == {"only in b"}

    db.close()


def test_retrieve_rejects_unknown_project(memory: MemoryService) -> None:
    with pytest.raises(ValueError, match="Unknown project id"):
        memory.retrieve(MemoryQuery(project_id=99999))


def test_query_builder() -> None:
    q = (
        MemoryQueryBuilder(1)
        .text("simulink check")
        .type("episode")
        .limit(5)
        .min_confidence(0.5)
        .tag("matlab")
        .build()
    )
    assert q.project_id == 1
    assert q.text == "simulink check"
    assert q.memory_type == "episode"
    assert q.limit == 5
    assert q.min_confidence == pytest.approx(0.5)
    assert q.tags == {"matlab"}


def test_memory_service_rejects_wrong_db() -> None:
    db = DatabaseService(":memory:")
    db.store_project("x", "x")
    class FakeDb:
        conn = db.conn

    with pytest.raises(TypeError, match="db must be a DatabaseService instance"):
        MemoryService(FakeDb())
    db.close()
