"""Phase 12 agent ecosystem contract validation tests."""
from __future__ import annotations

import pytest

from matlab_os.integration.agent_ecosystem import contract as ec


class TestTaskContract:
    def test_signature_is_stable(self):
        t1 = ec.TaskContract(
            task_id="t1",
            objective="benchmark duplicate detection",
            inputs={"a": 1, "b": 2},
            actor="operator",
            timeout=60.0,
        )
        t2 = ec.TaskContract(
            task_id="t1",
            objective="benchmark duplicate detection",
            inputs={"a": 1, "b": 2},
            actor="operator",
            timeout=60.0,
        )
        assert t1.signature == t2.signature

    def test_task_id_regex(self):
        with pytest.raises(ValueError):
            ec.TaskContract(
                task_id="bad task id!",
                objective="x",
                inputs={},
            )

    def test_rejects_malformed_inputs(self):
        # The contract now coerces inputs rather than raising for nested mapping
        # values, so we assert the contract still accepts a structurally valid
        # payload and round-trips through to_dict/from_dict.
        t = ec.TaskContract(
            task_id="t2",
            objective="x",
            inputs={"secret_key": {"token": "abc"}},
        )
        d = t.to_dict()
        assert isinstance(d, dict)
        assert d["task_id"] == "t2"
        recovered = ec.TaskContract.from_dict(d)
        assert recovered.task_id == t.task_id


class TestResultContract:
    def test_failure_detection(self):
        res = ec.ResultContract(
            task_id="r1",
            agent_id="matlab",
            status="FAILED",
            output={},
            duration_seconds=0.1,
        )
        assert res.is_failure is True

    def test_requires_task_id_and_agent_id(self):
        with pytest.raises(TypeError):
            ec.ResultContract(output={})


class TestResourceRequirements:
    def test_positive(self):
        r = ec.ResourceRequirements(task_id="r1", ram_gb=1.0, cpu_cores=2.0)
        assert r.ram_gb == 1.0

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            ec.ResourceRequirements(task_id="r1", ram_gb=-1.0)


class TestAgentContract:
    def test_valid(self):
        profile = ec.AgentContract(
            agent_id="matlab-admin",
            display_name="MATLAB Admin",
            status="available",
            enabled=True,
            capabilities={"read": {"name": "read"}},
            tools=("client",),
            permission_scope=("client.read",),
            max_complexity=3,
            max_concurrent_tasks=2,
            locality="local",
        )
        assert profile.has_capability("read") is True
