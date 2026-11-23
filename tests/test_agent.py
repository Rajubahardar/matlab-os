"""Agent: startup, skills, shared database schema, restart recovery."""
import pytest

from matlab_os.agent.matlab_agent import MATLABAgent
from matlab_os.agent.skills import get_skills


@pytest.fixture
def agent(tmp_path):
    a = MATLABAgent(db_path=str(tmp_path / "agent.db"))
    a.initialize()
    yield a
    a.close()


def test_not_ready_until_initialize_is_called(tmp_path):
    a = MATLABAgent(db_path=str(tmp_path / "a.db"))
    assert a.initialized is False
    assert a.get_skills() == []


def test_initialize_registers_all_sixteen_skills(agent):
    assert agent.initialized is True
    assert agent.get_skills() == get_skills()
    assert len(agent.get_skills()) == 16


def test_unregistered_skill_is_rejected(agent):
    with pytest.raises(ValueError):
        agent.execute_skill("Not A Skill")


def test_agent_uses_the_approval_gate_schema(agent):
    cols = {r[1] for r in agent.db.conn.execute("PRAGMA table_info(approvals)")}
    assert "payload_sha256" in cols


def test_close_releases_the_database(tmp_path):
    a = MATLABAgent(db_path=str(tmp_path / "c.db"))
    a.initialize()
    a.close()
    assert a.db is None and a.initialized is False


def test_project_context_survives_restart(tmp_path):
    path = str(tmp_path / "restart.db")
    first = MATLABAgent(db_path=path)
    first.initialize()
    pid = first.db.store_project("synchronverter", objectives="grid-connected PV")
    first.close()

    second = MATLABAgent(db_path=path)
    second.initialize()
    try:
        row = second.db.conn.execute(
            "SELECT name, objectives FROM projects WHERE id = ?", (pid,)).fetchone()
        assert row == ("synchronverter", "grid-connected PV")
    finally:
        second.close()


def test_evidence_is_linked_to_its_experiment(agent):
    pid = agent.db.store_project("p")
    eid = agent.db.store_experiment(pid, "EXP-1")
    evid = agent.db.store_evidence(eid, claim="RMS is 1/sqrt(2)",
                                   observation="0.70710678", source="MATLAB run")
    row = agent.db.conn.execute(
        "SELECT experiment_id, claim, source FROM evidence WHERE id = ?",
        (evid,)).fetchone()
    assert row == (eid, "RMS is 1/sqrt(2)", "MATLAB run")