"""SQLite Database Service for MATLAB OS"""
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import logging
import hashlib
from datetime import datetime, timedelta, timezone

APPROVAL_TTL = timedelta(minutes=15)

logger = logging.getLogger(__name__)


class DatabaseService:
    """SQLite-based persistence service for MATLAB OS."""

    def __init__(self, db_path: str = "data/matlab_agent.db", check_same_thread: bool = False):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=check_same_thread)
        self._create_tables()
        self._migrate_approvals()
        logger.info(f"Database service initialized at {self.db_path}")

    def _create_tables(self):
        """Create all required database tables."""
        cursor = self.conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS projects ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, "
            "objectives TEXT, "
            "status TEXT DEFAULT 'pending', "
            "matlab_version TEXT, "
            "dependencies TEXT, "
            "next_steps TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS experiments ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "project_id INTEGER, "
            "experiment_id TEXT, "
            "inputs TEXT, "
            "parameters TEXT, "
            "source_reference TEXT, "
            "status TEXT DEFAULT 'pending', "
            "outputs TEXT, "
            "errors TEXT, "
            "timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "FOREIGN KEY (project_id) REFERENCES projects(id))"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS evidence ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "experiment_id INTEGER, "
            "claim TEXT, "
            "observation TEXT, "
            "source TEXT, "
            "validated BOOLEAN DEFAULT FALSE, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "FOREIGN KEY (experiment_id) REFERENCES experiments(id))"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS approvals ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "operation TEXT NOT NULL, "
            "status TEXT DEFAULT 'pending', "
            "requested_by TEXT, "
            "approved BOOLEAN DEFAULT FALSE, "
            "reason TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS tasks ("
            "task_id TEXT PRIMARY KEY, "
            "project_id INTEGER DEFAULT 1, "
            "task TEXT NOT NULL, "
            "category TEXT DEFAULT '', "
            "status TEXT DEFAULT 'SUBMITTED', "
            "requested_by TEXT DEFAULT 'operator', "
            "parameters TEXT, "
            "result TEXT, "
            "error TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        self.conn.commit()

    def store_project(self, name: str, objectives: str = None,
                      matlab_version: str = None, dependencies: str = None,
                      next_steps: str = None, project_id: int | None = None) -> int:
        """Store a new project and return its ID."""
        cursor = self.conn.cursor()
        if project_id is None:
            cursor.execute(
                "INSERT INTO projects (name, objectives, matlab_version, dependencies, next_steps) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, objectives, matlab_version, dependencies, next_steps)
            )
        else:
            cursor.execute(
                "INSERT INTO projects (id, name, objectives, matlab_version, dependencies, next_steps) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, name, objectives, matlab_version, dependencies, next_steps)
            )
        self.conn.commit()
        return cursor.lastrowid

    def get_project_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get the most recent project with this exact name."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM projects WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)
        )
        row = cursor.fetchone()
        if row:
            return {"id": row[0], "name": row[1], "objectives": row[2],
                    "status": row[3], "matlab_version": row[4],
                    "dependencies": row[5], "next_steps": row[6],
                    "created_at": row[7], "updated_at": row[8]}
        return None

    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        """Get project by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = cursor.fetchone()
        if row:
            return {"id": row[0], "name": row[1], "objectives": row[2],
                    "status": row[3], "matlab_version": row[4],
                    "dependencies": row[5], "next_steps": row[6],
                    "created_at": row[7], "updated_at": row[8]}
        return None

    def store_experiment(self, project_id: int, experiment_id: str,
                         inputs: Dict = None, parameters: Dict = None,
                         source_reference: str = None) -> int:
        """Store an experiment and return its ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO experiments (project_id, experiment_id, inputs, parameters, source_reference) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, experiment_id,
             json.dumps(inputs) if inputs else None,
             json.dumps(parameters) if parameters else None,
             source_reference))
        self.conn.commit()
        return cursor.lastrowid

    def get_experiment(self, experiment_id: int) -> Optional[Dict[str, Any]]:
        """Get experiment by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,))
        row = cursor.fetchone()
        if row:
            return {"id": row[0], "project_id": row[1], "experiment_id": row[2],
                    "inputs": json.loads(row[3]) if row[3] else None,
                    "parameters": json.loads(row[4]) if row[4] else None,
                    "source_reference": row[5], "status": row[6],
                    "outputs": row[7], "errors": row[8],
                    "timestamp": row[8]}
        return None

    def get_experiments_by_project(self, project_id: int) -> List[Dict[str, Any]]:
        """Get all experiments for a project."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM experiments WHERE project_id = ?", (project_id,))
        rows = cursor.fetchall()
        experiments = []
        for row in rows:
            experiments.append({"id": row[0], "project_id": row[1], "experiment_id": row[2],
                                "inputs": json.loads(row[3]) if row[3] else None,
                                "parameters": json.loads(row[4]) if row[4] else None,
                                "source_reference": row[5], "status": row[6],
                                "outputs": row[7], "errors": row[8],
                                "timestamp": row[9]})
        return experiments

    def store_evidence(self, experiment_id: int, claim: str,
                       observation: str = None, source: str = None,
                       validated: bool = False) -> int:
        """Store evidence traceability entry."""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO evidence (experiment_id, claim, observation, source, validated) "
            "VALUES (?, ?, ?, ?, ?)",
            (experiment_id, claim, observation, source, validated))
        self.conn.commit()
        return cursor.lastrowid

    def _migrate_approvals(self):
        """Add approval-gate columns to an older approvals table (keeps data)."""
        cur = self.conn.cursor()
        have = {r[1] for r in cur.execute("PRAGMA table_info(approvals)")}
        for name in ("payload_sha256", "requested_at", "decided_by",
                     "decided_at", "used_at"):
            if name not in have:
                cur.execute(f"ALTER TABLE approvals ADD COLUMN {name} TEXT")
        self.conn.commit()

    def store_approval(self, operation: str, payload: str = "",
                       requested_by: str = None, reason: str = None,
                       *, status: str = "pending") -> int:
        """Create a PENDING approval bound to the SHA-256 of the payload."""
        if status != "pending":
            raise ValueError("approvals always start as 'pending'")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO approvals (operation, status, requested_by, reason, "
            "payload_sha256, requested_at) VALUES (?, 'pending', ?, ?, ?, ?)",
            (operation, requested_by, reason, digest,
             datetime.now(timezone.utc).isoformat()))
        self.conn.commit()
        return cur.lastrowid

    # shortcut: the decider is only a string, a hostile agent could pass any
    # name; upgrade to a separate user-run CLI when the agent loop is built.
    def approve_operation(self, approval_id: int, approved: bool,
                          decided_by: str, reason: str = None) -> bool:
        """Approve or reject a pending request. There is no default decision."""
        if not isinstance(approved, bool):
            raise TypeError("approved must be True or False")
        cur = self.conn.cursor()
        row = cur.execute("SELECT requested_by, status FROM approvals "
                          "WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            return False
        requested_by, status = row
        if status != "pending":
            raise ValueError(f"approval already {status}")
        if not decided_by or decided_by == requested_by:
            raise PermissionError("requester cannot decide their own request")
        cur.execute(
            "UPDATE approvals SET status = ?, approved = ?, decided_by = ?, "
            "decided_at = ?, reason = COALESCE(?, reason), "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            ("approved" if approved else "rejected", approved, decided_by,
             datetime.now(timezone.utc).isoformat(), reason, approval_id))
        self.conn.commit()
        return True

    def use_approval(self, approval_id: int, payload: str,
                     now: datetime = None) -> bool:
        """True once, only if approved, unexpired, unused and hash matches."""
        now = now or datetime.now(timezone.utc)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        cur = self.conn.cursor()
        row = cur.execute("SELECT status, payload_sha256, decided_at "
                          "FROM approvals WHERE id = ?",
                          (approval_id,)).fetchone()
        if row is None:
            return False
        status, stored, decided_at = row
        if status != "approved" or stored != digest or not decided_at:
            return False
        if now > datetime.fromisoformat(decided_at) + APPROVAL_TTL:
            return False
        cur.execute("UPDATE approvals SET status = 'used', used_at = ? "
                    "WHERE id = ? AND status = 'approved'",
                    (now.isoformat(), approval_id))
        self.conn.commit()
        return cur.rowcount == 1

    def store_task(self, task_id: str, task: str, status: str = "SUBMITTED",
                   parameters: Optional[Dict] = None, requested_by: str = "operator",
                   project_id: int = 1) -> str:
        """Store or replace a persistent task record."""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO tasks (task_id, project_id, task, status, requested_by, parameters) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, project_id, task, status, requested_by,
             json.dumps(parameters) if parameters is not None else None)
        )
        self.conn.commit()
        return task_id

    def update_task(self, task_id: str, status: Optional[str] = None,
                    result: Any = None, error: Optional[str] = None,
                    parameters: Optional[Dict] = None) -> bool:
        """Update fields of an existing task."""
        cursor = self.conn.cursor()
        updates = []
        params = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if result is not None:
            updates.append("result = ?")
            params.append(json.dumps(result) if not isinstance(result, str) else result)
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        if parameters is not None:
            updates.append("parameters = ?")
            params.append(json.dumps(parameters))
        updates.append("updated_at = CURRENT_TIMESTAMP")
        if not updates:
            return False
        params.append(task_id)
        cursor.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?", tuple(params))
        self.conn.commit()
        return cursor.rowcount > 0

    def claim_task_for_execution(self, task_id: str) -> bool:
        """Atomically transition task from WAITING_FOR_APPROVAL -> RUNNING.
        Returns True iff this connection successfully performed the transition.
        Safe across concurrent threads and processes; SQLite serializes writes.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tasks "
            "SET status = 'RUNNING', updated_at = CURRENT_TIMESTAMP "
            "WHERE task_id = ? AND status = 'WAITING_FOR_APPROVAL'",
            (task_id,)
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def recover_abandoned_tasks(self, timeout_seconds: int = 180) -> int:
        """Mark stale RUNNING tasks as FAILED on startup in single-worker mode."""
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tasks "
            "SET status = 'FAILED', "
            "    error = 'Execution interrupted by server restart', "
            "    updated_at = CURRENT_TIMESTAMP "
            "WHERE status = 'RUNNING'"
        )
        self.conn.commit()
        return cursor.rowcount

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a task by ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT task_id, project_id, task, category, status, requested_by, parameters, result, error, created_at, updated_at "
            "FROM tasks WHERE task_id = ?",
            (task_id,)
        )
        row = cursor.fetchone()
        if row:
            params = json.loads(row[6]) if row[6] else {}
            res = row[7]
            if res is not None and isinstance(res, str):
                try:
                    res = json.loads(res)
                except Exception:
                    pass
            return {
                "task_id": row[0],
                "project_id": row[1],
                "task": row[2],
                "category": row[3],
                "status": row[4],
                "requested_by": row[5],
                "parameters": params,
                "result": res,
                "error": row[8],
                "created_at": row[9],
                "updated_at": row[10],
            }
        return None

    def list_tasks(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List tasks with optional status filter."""
        cursor = self.conn.cursor()
        if status:
            cursor.execute(
                "SELECT task_id, project_id, task, category, status, requested_by, parameters, result, error, created_at, updated_at "
                "FROM tasks WHERE status = ? ORDER BY created_at DESC",
                (status,)
            )
        else:
            cursor.execute(
                "SELECT task_id, project_id, task, category, status, requested_by, parameters, result, error, created_at, updated_at "
                "FROM tasks ORDER BY created_at DESC"
            )
        rows = cursor.fetchall()
        tasks = []
        for row in rows:
            params = json.loads(row[6]) if row[6] else {}
            res = row[7]
            if res is not None and isinstance(res, str):
                try:
                    res = json.loads(res)
                except Exception:
                    pass
            tasks.append({
                "task_id": row[0],
                "project_id": row[1],
                "task": row[2],
                "category": row[3],
                "status": row[4],
                "requested_by": row[5],
                "parameters": params,
                "result": res,
                "error": row[8],
                "created_at": row[9],
                "updated_at": row[10],
            })
        return tasks

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
        logger.info("Database service closed.")
