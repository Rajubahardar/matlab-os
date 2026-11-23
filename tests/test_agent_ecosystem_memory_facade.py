"""Phase 12 controlled memory access tests."""
from __future__ import annotations

import pytest

from matlab_os.memory.database import DatabaseService
from matlab_os.integration.agent_ecosystem import memory_facade as ec_memory


class TestMemoryFacade:
    def test_write_and_read(self, tmp_path):
        db = DatabaseService(db_path=str(tmp_path / "memory.db"))
        fac = ec_memory.MemoryFacade(db, workspace_root=tmp_path)
        write_result = fac.write_task_context("task:abc", "operator", {"parameter": 42})
        read = fac.read_task_context("task:abc", "operator")
        assert read["context"]["parameter"] == 42
        assert read["task_id"] == "task:abc"
        assert "project_id" in write_result
        assert "experiment_id" in write_result

    def test_no_silent_overwrite(self):
        db = DatabaseService(db_path=":memory:")
        fac = ec_memory.MemoryFacade(db, workspace_root="/tmp")
        fac.write_task_context("task:abc", "operator", {"parameter": 42})
        with pytest.raises(ec_memory.ConflictError):
            fac.write_task_context("task:abc", "operator", {"parameter": 43})

    def test_foreign_write_blocked(self):
        db = DatabaseService(db_path=":memory:")
        fac = ec_memory.MemoryFacade(db, workspace_root="/tmp")
        fac.write_task_context("task:abc", "operator", {"parameter": 42})
        with pytest.raises(ec_memory.ConflictError):
            fac.write_task_context("task:abc", "operator", {"parameter": 99})

    def test_read_missing_context(self):
        db = DatabaseService(db_path=":memory:")
        fac = ec_memory.MemoryFacade(db, workspace_root="/tmp")
        with pytest.raises(ec_memory.NotFoundError):
            fac.read_task_context("task:missing", "operator")

    def test_read_after_write(self):
        db = DatabaseService(db_path=":memory:")
        fac = ec_memory.MemoryFacade(db, workspace_root="/tmp")
        fac.write_task_context("task:abc", "operator", {"parameter": 42})
        read = fac.read_task_context("task:abc", "operator")
        assert read["context"]["parameter"] == 42
        assert read["task_id"] == "task:abc"

