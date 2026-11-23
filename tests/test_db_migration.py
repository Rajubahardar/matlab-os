"""An old-schema approvals table must be upgraded in place, keeping its rows."""
import sqlite3

from matlab_os.memory.database import DatabaseService

OLD_SCHEMA = (
    "CREATE TABLE approvals ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT, status TEXT, "
    "requested_by TEXT, reason TEXT, approved BOOLEAN, updated_at TIMESTAMP)"
)


def test_old_approvals_table_is_migrated_and_rows_survive(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(OLD_SCHEMA)
    conn.execute("INSERT INTO approvals (operation, status, requested_by) "
                 "VALUES ('old_op', 'pending', 'someone')")
    conn.commit()
    conn.close()

    db = DatabaseService(db_path=str(path))
    try:
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(approvals)")}
        assert {"payload_sha256", "requested_at", "decided_by",
                "decided_at", "used_at"} <= cols
        old = db.conn.execute(
            "SELECT operation FROM approvals WHERE requested_by = 'someone'"
        ).fetchone()
        assert old == ("old_op",)
        new_id = db.store_approval("new_op", payload="x", requested_by="agent")
        assert new_id > 0
    finally:
        db.close()