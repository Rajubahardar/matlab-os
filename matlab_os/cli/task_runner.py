"""Task Runner for Local AI OS V1.

Handles task submission, status checking, result retrieval, and history.
Uses the DatabaseService for persistence across CLI invocations.
"""

import os
from matlab_os.memory.database import DatabaseService


class TaskRunner:
    """Manages task lifecycle with persistent storage.

    Tasks survive CLI process exits because records are stored in SQLite via
    DatabaseService. Each task has a unique submission_id, a status, an optional
    result, and a history log of events.
    """

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            # Default to data/matlab_agent.db relative to the repo root
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(repo_root, "data", "matlab_agent.db")
        self.db = DatabaseService(db_path=db_path)
        # Ensure the tasks table exists for persistence
        self._ensure_tasks_table()

    def _ensure_tasks_table(self):
        """Ensure the tasks table exists in the database."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'pending',
                result TEXT,
                history TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        self.db.conn.commit()

    # ---- Submission ----
    def submit(self, description: str, metadata: dict | None = None) -> str:
        """Submit a new task and return its submission_id."""
        import uuid
        submission_id = str(uuid.uuid4())
        history_entry = f"submitted: {description}"
        self.db.store_task(
            submission_id=submission_id,
            status="pending",
            result=None,
            history=history_entry,
        )
        # Also store metadata as part of the task if provided
        if metadata:
            import json
            existing = self.db.get_task(submission_id)
            if existing and existing.get("result") is not None:
                merged = {**existing["result"], **metadata}
                self.db.update_task_status(
                    submission_id=submission_id,
                    status="pending",
                    result=merged,
                    history_entry=f"metadata added: {json.dumps(metadata)}",
                )
            else:
                self.db.update_task_status(
                    submission_id=submission_id,
                    status="pending",
                    result=metadata,
                    history_entry=f"metadata added: {json.dumps(metadata)}",
                )
        return submission_id

    # ---- Status ----
    def status(self, submission_id: str) -> dict | None:
        """Return the current status and metadata for a task."""
        return self.db.get_task(submission_id)

    # ---- Results ----
    def retrieve(self, submission_id: str) -> dict | None:
        """Return the result for a completed task."""
        task = self.db.get_task(submission_id)
        if task is None:
            return None
        if task["status"] in ("completed", "failed", "rejected"):
            return {"status": task["status"], "result": task.get("result")}
        return {"status": task["status"], "result": None}

    # ---- History ----
    def history(self, submission_id: str) -> list:
        """Return the task history as a list of event strings."""
        task = self.db.get_task(submission_id)
        if task is None:
            return []
        return task.get("history", []) or []

    # ---- List all tasks ----
    def list_all(self, status: str | None = None) -> list:
        """List tasks, optionally filtered by status."""
        return self.db.list_tasks(status=status)

    def close(self):
        """Close the database connection."""
        self.db.close()