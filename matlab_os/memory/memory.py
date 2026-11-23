"""Unified long-term memory for Local AI OS.

Phase 16 memory provides a controlled, project-scoped memory surface on top of the
existing SQLite ``DatabaseService``.  It is intentionally a facade: durable storage
still lives in ``DatabaseService``, and this module adds memory-specific schema and
operations (episodes, facts, retrieval, ranking, forgetting, provenance).

Design notes
------------
* Memory is scoped by project.  A memory operation without a project is rejected so
  that memory does not silently become global state.
* Every stored memory carries provenance metadata (author, source, created_at,
  updated_at) so later consumers can trace where a memory came from.
* Retrieval is intentionally simple: text search over the memory payload plus a small
  ranking heuristic over recency, confidence, and relevance.  This is enough for a
  personal system and keeps the surface small.
* Forgetting is explicit.  Memory is never silently overwritten or evicted without an
  operation.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .database import DatabaseService

logger = logging.getLogger(__name__)

_MEMORY_SCHEMA_CREATED = "_memory_schema_created"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _utc_now().isoformat()


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _text_hash(text: str) -> str:
    return hashlib.sha256(_normalize_text(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryRecord:
    """One memory entry returned by the memory facade."""

    memory_id: int
    project_id: int
    memory_type: str
    key: Optional[str]
    content: str
    author: Optional[str]
    source: Optional[str]
    confidence: float
    created_at: str
    updated_at: str
    tags: list[str]
    meta: dict[str, Any]


@dataclass(frozen=True)
class MemoryQuery:
    """A read/query specification for memory retrieval."""

    project_id: int
    text: Optional[str] = None
    memory_type: Optional[str] = None
    limit: int = 20
    offset: int = 0
    prefer_recent: bool = True
    min_confidence: float = 0.0
    tags: Optional[set[str]] = None


class MemoryService:
    """Unified memory facade scoped by project.

    Usage
    -----
    ::

        db = DatabaseService(":memory:")
        mem = MemoryService(db)
        pid = db.store_project("demo", "objectives")
        mem.store_episode(pid, "I ran the Simulink check and it failed on step 3.")
        mem.store_fact(pid, key="simulink.last_check", content="failed at step 3")
        mem.retrieve(pid, text="Simulink check")
    """

    def __init__(self, db: DatabaseService) -> None:
        if not isinstance(db, DatabaseService):
            raise TypeError("db must be a DatabaseService instance.")
        self._db = db
        self._ensure_memory_tables()

    # ------------------------------------------------------------------ #
    # Schema
    # ------------------------------------------------------------------ #

    def _ensure_memory_tables(self) -> None:
        cur = self._db.conn.cursor()
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS memory ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "project_id INTEGER NOT NULL, "
            "memory_type TEXT NOT NULL, "
            "memory_key TEXT, "
            "content TEXT NOT NULL, "
            "author TEXT, "
            "source TEXT, "
            "confidence REAL DEFAULT 0.5, "
            "tags TEXT, "
            "meta TEXT, "
            "created_at TIMESTAMP NOT NULL, "
            "updated_at TIMESTAMP NOT NULL, "
            "FOREIGN KEY (project_id) REFERENCES projects(id))"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS memory_project_type "
            "ON memory (project_id, memory_type)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS memory_project_key "
            "ON memory (project_id, memory_key)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS memory_project_created "
            "ON memory (project_id, created_at)"
        )
        self._db.conn.commit()

    # ------------------------------------------------------------------ #
    # Episodes
    # ------------------------------------------------------------------ #

    def store_episode(
        self,
        project_id: int,
        content: str,
        *,
        author: Optional[str] = None,
        source: Optional[str] = None,
        confidence: float = 0.5,
        tags: Optional[set[str]] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> int:
        """Store an episodic memory observation for a project."""
        if not self._project_exists(project_id):
            raise ValueError(f"Unknown project id: {project_id}")
        return self._store_memory(
            project_id=project_id,
            memory_type="episode",
            content=_normalize_text(content),
            author=author,
            source=source,
            confidence=float(confidence),
            tags=tags,
            meta=meta,
        )

    # ------------------------------------------------------------------ #
    # Facts
    # ------------------------------------------------------------------ #

    def store_fact(
        self,
        project_id: int,
        key: str,
        content: str,
        *,
        author: Optional[str] = None,
        source: Optional[str] = None,
        confidence: float = 0.5,
        tags: Optional[set[str]] = None,
        meta: Optional[dict[str, Any]] = None,
        replace: bool = True,
    ) -> int:
        """Store a keyed fact for a project.

        When ``replace`` is true and a fact with the same key already exists for the
        project, the existing fact is updated (preserving its id) and its confidence is
        not lowered unless explicitly passed.
        """
        if not self._project_exists(project_id):
            raise ValueError(f"Unknown project id: {project_id}")
        existing = self._find_fact(project_id, key)
        if existing and not replace:
            return existing["id"]
        if existing and existing.get("confidence", 0.0) > float(confidence):
            confidence = existing["confidence"]
        if existing:
            return self._update_memory(
                existing["id"],
                content=_normalize_text(content),
                author=author,
                source=source,
                confidence=float(confidence),
                tags=tags,
                meta=meta,
            )
        return self._store_memory(
            project_id=project_id,
            memory_type="fact",
            memory_key=str(key),
            content=_normalize_text(content),
            author=author,
            source=source,
            confidence=float(confidence),
            tags=tags,
            meta=meta,
        )

    def get_fact(self, project_id: int, key: str) -> Optional[MemoryRecord]:
        """Return the current fact for a project key, or None."""
        row = self._find_fact(project_id, key)
        if row is None:
            return None
        return self._row_to_record(row)

    def forget_fact(self, project_id: int, key: str) -> bool:
        """Forget a fact by key. Returns True when a fact was removed."""
        if not self._project_exists(project_id):
            raise ValueError(f"Unknown project id: {project_id}")
        cur = self._db.conn.cursor()
        cur.execute(
            "DELETE FROM memory WHERE project_id = ? AND memory_type = 'fact' "
            "AND memory_key = ?",
            (project_id, str(key)),
        )
        self._db.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #

    def retrieve(self, query: MemoryQuery) -> list[MemoryRecord]:
        """Retrieve and rank memories for a project.

        Ranking is a simple heuristic: results are filtered by type/tags/confidence,
        then ordered by a combined score of recency and confidence, with optional
        text-match bonus.
        """
        if not self._project_exists(query.project_id):
            raise ValueError(f"Unknown project id: {query.project_id}")
        rows = self._query_rows(query)
        if not rows:
            return []
        scored = []
        text_tokens = _normalize_text(query.text).lower().split()
        text_tokens = [t for t in text_tokens if t]
        for row in rows:
            record = self._row_to_record(row)
            score = self._score(record, text_tokens)
            if score <= 0.0:
                continue
            scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        selected = [record for _, record in scored]
        if query.offset:
            selected = selected[query.offset:]
        if query.limit:
            selected = selected[:query.limit]
        return selected

    def list_memories(
        self,
        project_id: int,
        *,
        memory_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        order_by: str = "created_at",
    ) -> list[MemoryRecord]:
        """List memories for a project in storage order."""
        if not self._project_exists(project_id):
            raise ValueError(f"Unknown project id: {project_id}")
        allowed_order = {"created_at", "updated_at", "confidence", "id"}
        order_col = order_by if order_by in allowed_order else "created_at"
        cur = self._db.conn.cursor()
        sql = "SELECT * FROM memory WHERE project_id = ?"
        params: list[Any] = [project_id]
        if memory_type:
            sql += " AND memory_type = ?"
            params.append(memory_type)
        sql += f" ORDER BY {order_col} DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        if offset:
            sql += " OFFSET ?"
            params.append(int(offset))
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        records = []
        for row in rows:
            record_dict = dict(zip(cols, row))
            records.append(self._row_to_record(record_dict))
        return records

    # ------------------------------------------------------------------ #
    # Forgetting / maintenance
    # ------------------------------------------------------------------ #

    def forget(self, project_id: int, memory_id: int) -> bool:
        """Forget one memory by id. Returns True when a memory was removed."""
        if not self._project_exists(project_id):
            raise ValueError(f"Unknown project id: {project_id}")
        cur = self._db.conn.cursor()
        cur.execute(
            "DELETE FROM memory WHERE id = ? AND project_id = ?",
            (memory_id, project_id),
        )
        self._db.conn.commit()
        return cur.rowcount > 0

    def forget_type(self, project_id: int, memory_type: str) -> int:
        """Forget all memories of a type for a project. Returns count removed."""
        if not self._project_exists(project_id):
            raise ValueError(f"Unknown project id: {project_id}")
        cur = self._db.conn.cursor()
        cur.execute(
            "DELETE FROM memory WHERE project_id = ? AND memory_type = ?",
            (project_id, memory_type),
        )
        self._db.conn.commit()
        return cur.rowcount

    def memory_counts(self, project_id: int) -> dict[str, Any]:
        """Return memory usage counts for a project."""
        if not self._project_exists(project_id):
            raise ValueError(f"Unknown project id: {project_id}")
        cur = self._db.conn.cursor()
        cur.execute(
            "SELECT memory_type, COUNT(*) FROM memory "
            "WHERE project_id = ? GROUP BY memory_type",
            (project_id,),
        )
        by_type: dict[str, int] = {}
        total = 0
        for row in cur.fetchall():
            by_type[str(row[0])] = int(row[1])
            total += int(row[1])
        return {"total": total, "by_type": by_type, "project_id": project_id}

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _project_exists(self, project_id: int) -> bool:
        row = self._db.conn.cursor().execute(
            "SELECT 1 FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return row is not None

    def _store_memory(
        self,
        *,
        project_id: int,
        memory_type: str,
        content: str,
        memory_key: Optional[str] = None,
        author: Optional[str] = None,
        source: Optional[str] = None,
        confidence: float = 0.5,
        tags: Optional[set[str]] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> int:
        now = _now_iso()
        tags_json = json.dumps(sorted(set(tags))) if tags else "[]"
        meta_json = json.dumps(meta) if meta else "{}"
        cur = self._db.conn.cursor()
        cur.execute(
            "INSERT INTO memory "
            "(project_id, memory_type, memory_key, content, author, source, "
            "confidence, tags, meta, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                memory_type,
                memory_key,
                content,
                author,
                source,
                float(confidence),
                tags_json,
                meta_json,
                now,
                now,
            ),
        )
        self._db.conn.commit()
        return int(cur.lastrowid)

    def _update_memory(
        self,
        memory_id: int,
        *,
        content: Optional[str] = None,
        author: Optional[str] = None,
        source: Optional[str] = None,
        confidence: Optional[float] = None,
        tags: Optional[set[str]] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> int:
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [_now_iso()]
        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if author is not None:
            sets.append("author = ?")
            params.append(author)
        if source is not None:
            sets.append("source = ?")
            params.append(source)
        if confidence is not None:
            sets.append("confidence = ?")
            params.append(float(confidence))
        if tags is not None:
            sets.append("tags = ?")
            params.append(json.dumps(sorted(set(tags))))
        if meta is not None:
            sets.append("meta = ?")
            params.append(json.dumps(meta))
        params.append(memory_id)
        cur = self._db.conn.cursor()
        cur.execute(
            f"UPDATE memory SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        self._db.conn.commit()
        return memory_id

    def _find_fact(self, project_id: int, key: str) -> Optional[dict[str, Any]]:
        cur = self._db.conn.cursor()
        cur.execute(
            "SELECT * FROM memory WHERE project_id = ? AND memory_type = 'fact' "
            "AND memory_key = ? ORDER BY updated_at DESC LIMIT 1",
            (project_id, str(key)),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def _query_rows(self, query: MemoryQuery) -> list[dict[str, Any]]:
        cur = self._db.conn.cursor()
        sql = "SELECT * FROM memory WHERE project_id = ?"
        params: list[Any] = [query.project_id]
        if query.memory_type:
            sql += " AND memory_type = ?"
            params.append(query.memory_type)
        if query.min_confidence:
            sql += " AND confidence >= ?"
            params.append(float(query.min_confidence))
        if query.tags:
            conditions = []
            tag_params: list[Any] = []
            for tag in sorted(query.tags):
                conditions.append("tags LIKE ?")
                tag_params.append(f'%"{tag}"%')
            sql += " AND (" + " OR ".join(conditions) + ")"
            params.extend(tag_params)
        if query.text:
            row = self._db.conn.cursor().execute(
                "SELECT id FROM memory WHERE project_id = ? "
                "AND (content LIKE ? OR memory_key LIKE ?)",
                (
                    query.project_id,
                    f'%{query.text}%',
                    f'%{query.text}%',
                ),
            ).fetchall()
            matched_ids = {r[0] for r in row}
            if matched_ids:
                sql += " AND id IN ({})".format(",".join("?" for _ in matched_ids))
                params.extend(sorted(matched_ids))
        sql += " ORDER BY updated_at DESC"
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    @staticmethod
    def _score(record: MemoryRecord, text_tokens: list[str]) -> float:
        if not text_tokens:
            return record.confidence
        content = record.content.lower()
        key = (record.key or "").lower()
        text_bonus = 0.0
        for token in text_tokens:
            if token and (token in content or token in key):
                text_bonus += 1.0
        recency = 1.0
        try:
            age_seconds = (_utc_now() - datetime.fromisoformat(record.updated_at)).total_seconds()
            recency = 1.0 / (1.0 + age_seconds / 3600.0)
        except Exception:
            recency = 0.5
        relevance = min(1.0, text_bonus / max(1, len(text_tokens)))
        return 0.5 * recency + 0.3 * relevance + 0.2 * float(record.confidence)

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> MemoryRecord:
        tags_raw = row.get("tags") or "[]"
        try:
            tags = json.loads(tags_raw)
        except Exception:
            tags = []
        if not isinstance(tags, list):
            tags = []
        meta_raw = row.get("meta") or "{}"
        try:
            meta = json.loads(meta_raw)
        except Exception:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        return MemoryRecord(
            memory_id=int(row["id"]),
            project_id=int(row["project_id"]),
            memory_type=str(row["memory_type"]),
            key=row.get("memory_key"),
            content=str(row["content"]),
            author=row.get("author"),
            source=row.get("source"),
            confidence=float(row.get("confidence", 0.5) or 0.5),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            tags=[str(t) for t in tags],
            meta=meta,
        )


class MemoryQueryBuilder:
    """Convenience builder for :class:`MemoryQuery`."""

    def __init__(self, project_id: int) -> None:
        self._project_id = int(project_id)
        self._text: Optional[str] = None
        self._memory_type: Optional[str] = None
        self._limit: int = 20
        self._offset: int = 0
        self._prefer_recent: bool = True
        self._min_confidence: float = 0.0
        self._tags: set[str] = set()

    def text(self, text: str) -> "MemoryQueryBuilder":
        self._text = str(text)
        return self

    def type(self, memory_type: str) -> "MemoryQueryBuilder":
        self._memory_type = str(memory_type)
        return self

    def limit(self, limit: int) -> "MemoryQueryBuilder":
        self._limit = int(limit)
        return self

    def offset(self, offset: int) -> "MemoryQueryBuilder":
        self._offset = int(offset)
        return self

    def min_confidence(self, value: float) -> "MemoryQueryBuilder":
        self._min_confidence = float(value)
        return self

    def tag(self, tag: str) -> "MemoryQueryBuilder":
        self._tags.add(str(tag))
        return self

    def build(self) -> MemoryQuery:
        return MemoryQuery(
            project_id=self._project_id,
            text=self._text,
            memory_type=self._memory_type,
            limit=self._limit,
            offset=self._offset,
            prefer_recent=self._prefer_recent,
            min_confidence=self._min_confidence,
            tags=self._tags or None,
        )
