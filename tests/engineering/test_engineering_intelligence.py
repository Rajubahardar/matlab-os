"""Tests for Phase 15 MATLAB / Simulink engineering intelligence.

These tests are localized and deterministic: they exercise the contract model,
the simulation verifier, and the engineering-memory bridge without requiring a
real MATLAB MCP server.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

from matlab_os.engineering import (
    ContractValidationError,
    EngineeringError,
    EngineeringMemory,
    ExecutionTarget,
    SimulationResult,
    SimulationResultVerified,
    SimulationVerifier,
    TaskContract,
    VerificationError,
)


# ---------------------------------------------------------------------------
# Task contracts
# ---------------------------------------------------------------------------

class TestExecutionTarget:
    def test_requires_absolute_path(self) -> None:
        with pytest.raises(EngineeringError, match="absolute Path"):
            ExecutionTarget(workspace_root=Path("not_abs"), task_id="t1", tool="sim")

    def test_requires_non_empty_strings(self) -> None:
        base = Path(__file__).resolve().parent
        with pytest.raises(EngineeringError, match="task_id"):
            ExecutionTarget(workspace_root=base, task_id="", tool="sim")

        with pytest.raises(EngineeringError, match="tool"):
            ExecutionTarget(workspace_root=base, task_id="t1", tool="")

    def test_representations_are_stable(self) -> None:
        t = ExecutionTarget(
            workspace_root=Path(__file__).resolve().parent,
            task_id="task-1",
            tool="sim",
            target={"model": "spring.mdl"},
        )
        r = repr(t)
        assert "workspace=" + str(Path(__file__).resolve().parent) in r
        assert "task='task-1'" in r
        assert "tool='sim'" in r


class TestTaskContract:
    @pytest.fixture
    def workspace(self) -> Path:
        return Path(__file__).resolve().parent

    def test_valid_contract_is_validated(self, workspace: Path) -> None:
        c = TaskContract(
            contract_id="c1",
            task_id="t1",
            objective="Simulate the spring-mass system",
            operation="simulate",
            tool="sim",
            target={"model": "spring.mdl"},
            target_kind="matlab_script",
            output_expectation="a numeric scalar result",
            required_capabilities=["simulation"],
            workspace_root=workspace,
        )
        validated = c.validated()
        assert validated is c
        assert validated.target_kind == "matlab_script"

    def test_rejects_missing_contract_id(self, workspace: Path) -> None:
        with pytest.raises(ContractValidationError, match="contract_id"):
            TaskContract(
                contract_id="",
                task_id="t1",
                objective="x",
                operation="simulate",
                tool="sim",
                workspace_root=workspace,
            )

    def test_rejects_missing_tool(self, workspace: Path) -> None:
        with pytest.raises(ContractValidationError, match="tool"):
            TaskContract(
                contract_id="c1",
                task_id="t1",
                objective="x",
                operation="simulate",
                tool="",
                workspace_root=workspace,
            )

    def test_rejects_invalid_target_kind(self, workspace: Path) -> None:
        with pytest.raises(ContractValidationError, match="target_kind"):
            TaskContract(
                contract_id="c1",
                task_id="t1",
                objective="x",
                operation="run",
                tool="sim",
                target_kind="imaginary_kind",
                workspace_root=workspace,
            )

    def test_snapshot_roundtrips_core_fields(self, workspace: Path) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        c = TaskContract(
            contract_id="c1",
            task_id="t1",
            objective="obj",
            operation="simulate",
            tool="sim",
            target={"a": 1},
            target_kind="matlab_script",
            output_expectation="res",
            required_capabilities=["simulation", "verification"],
            workspace_root=workspace,
            version=3,
            created_by="raj",
            created_at=now,
        )
        s = c.snapshot()
        assert s["contract_id"] == "c1"
        assert s["task_id"] == "t1"
        assert s["objective"] == "obj"
        assert s["operation"] == "simulate"
        assert s["tool"] == "sim"
        assert s["target"] == {"a": 1}
        assert s["target_kind"] == "matlab_script"
        assert s["output_expectation"] == "res"
        assert s["required_capabilities"] == ["simulation", "verification"]
        assert s["workspace_root"] == str(workspace)
        assert s["version"] == 3
        assert s["created_by"] == "raj"
        assert s["created_at"] == now.isoformat()

    def test_execution_target_matches_contract(self, workspace: Path) -> None:
        c = TaskContract(
            contract_id="c1",
            task_id="t1",
            objective="obj",
            operation="simulate",
            tool="sim",
            target={"model": "spring.mdl"},
            target_kind="simulink_model",
            workspace_root=workspace,
        )
        t = c.as_execution_target()
        assert t.task_id == c.task_id
        assert t.tool == c.tool
        assert t.target == c.target
        assert t.target_kind == c.target_kind
        assert t.workspace_root == c.workspace_root


# ---------------------------------------------------------------------------
# Simulation results and verification
# ---------------------------------------------------------------------------

class TestSimulationResult:
    def test_default_invocation_time_is_now(self, any_time: datetime) -> None:
        r = SimulationResult(result_id="r1", task_id="t1", tool="sim", success=True)
        assert r.invoked_at.tzinfo is not None

    def test_successful_result_has_output(self) -> None:
        r = SimulationResult(
            result_id="r1",
            task_id="t1",
            tool="sim",
            success=True,
            output={"x": 1.0},
            signals={"position": [0.0, 1.0]},
        )
        assert r.is_successful()
        assert r.output["x"] == 1.0


class AnyTime:
    value: datetime

    def __init__(self) -> None:
        self.value = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def any_time() -> datetime:
    return AnyTime().value


class TestSimulationVerifier:
    def test_passes_minimal_successful_result(self, any_time: datetime) -> None:
        result = SimulationResult(
            result_id="r1",
            task_id="t1",
            tool="sim",
            success=True,
            output=[1.0, 2.0],
            stdout_text="done",
            invoked_at=any_time,
        )
        v = SimulationVerifier().verify(result)
        assert not v.failed_checks
        assert "structure" in v.passed_checks
        assert "success" in v.passed_checks
        assert v.is_successful()

    def test_fails_when_tool_reported_error(self, any_time: datetime) -> None:
        result = SimulationResult(
            result_id="r1",
            task_id="t1",
            tool="sim",
            success=False,
            error="timeout",
            output=None,
            invoked_at=any_time,
        )
        v = SimulationVerifier().verify(result)
        assert "success" in v.failed_checks
        assert not v.is_successful()

    def test_demands_numeric_output(self, any_time: datetime) -> None:
        result = SimulationResult(
            result_id="r1",
            task_id="t1",
            tool="sim",
            success=True,
            output={"x": "not a number"},
            invoked_at=any_time,
        )
        v = SimulationVerifier().verify(
            result, expectation={"require_numeric_output": True}
        )
        assert "output_kind" in v.failed_checks
        assert not v.is_successful()

    def test_demands_mapping_output(self, any_time: datetime) -> None:
        result = SimulationResult(
            result_id="r1",
            task_id="t1",
            tool="sim",
            success=True,
            output=[1, 2, 3],
            invoked_at=any_time,
        )
        v = SimulationVerifier().verify(
            result, expectation={"require_mapping_output": True}
        )
        assert "output_kind" in v.failed_checks
        assert not v.is_successful()

    def test_demands_list_output(self, any_time: datetime) -> None:
        result = SimulationResult(
            result_id="r1",
            task_id="t1",
            tool="sim",
            success=True,
            output={"a": 1},
            invoked_at=any_time,
        )
        v = SimulationVerifier().verify(
            result, expectation={"require_list_output": True}
        )
        assert "output_kind" in v.failed_checks
        assert not v.is_successful()

    def test_requires_expected_output_keys(self, any_time: datetime) -> None:
        result = SimulationResult(
            result_id="r1",
            task_id="t1",
            tool="sim",
            success=True,
            output={"x": 1.0},
            invoked_at=any_time,
        )
        v = SimulationVerifier().verify(
            result,
            expectation={"output_keys": ["x", "y"]},
        )
        assert "output_kind" in v.failed_checks

    def test_log_counts_verified_decisions(self, any_time: datetime) -> None:
        v = SimulationVerifier()
        # First failure
        v.verify(
            SimulationResult(
                result_id="r1", task_id="t1", tool="sim", success=False, invoked_at=any_time
            )
        )
        # Then success
        v.verify(
            SimulationResult(
                result_id="r2", task_id="t1", tool="sim", success=True, output=[1.0], invoked_at=any_time
            )
        )
        assert v.checks_total() == 2
        assert v.checks_passed() == 1


class TestSimulationResultVerified:
    def test_success_flag_mirrors_failed_checks(self, any_time: datetime) -> None:
        base = SimulationResult(
            result_id="r1", task_id="t1", tool="sim", success=True, output={"x": 1.0}, invoked_at=any_time
        )
        good = SimulationResultVerified(base, passed_checks=["structure", "success"])
        assert good.is_successful()

        bad = SimulationResultVerified(base, passed_checks=["structure"], failed_checks=["success"])
        assert not bad.is_successful()


# ---------------------------------------------------------------------------
# Engineering memory bridge
# ---------------------------------------------------------------------------


class DummyMemory:
    def __init__(self) -> None:
        self.episodes: list[dict[str, Any]] = []

    def store_episode(
        self,
        *,
        project_id: int,
        content: str = "",
        record_type: str = "episode",
        tags: set[str] | None = None,
        confidence: float = 0.0,
        meta: Mapping[str, Any] | None = None,
    ) -> int:
        self.episodes.append(
            {
                "project_id": project_id,
                "content": content,
                "record_type": record_type,
                "tags": set(tags or set()),
                "confidence": confidence,
                "meta": dict(meta or {}),
            }
        )
        return len(self.episodes)


class TestEngineeringMemory:
    def test_rejects_non_memory_service(self) -> None:
        class NotAMemory:
            store_episode = 7

        with pytest.raises(EngineeringError, match="MemoryService-like"):
            EngineeringMemory(NotAMemory())

    def test_records_operation(self, project_fixture) -> None:
        mem = DummyMemory()
        em = EngineeringMemory(mem)
        contract = TaskContract(
            contract_id="c1",
            task_id="t1",
            objective="obj",
            operation="simulate",
            tool="sim",
            target_kind="matlab_script",
            workspace_root=Path(__file__).resolve().parent,
        )
        out = em.record_operation(
            project_id=project_fixture,
            contract_snapshot=contract.snapshot(),
            operation="simulate",
            tool="sim",
            outcome={"returned": True},
            reason="approved",
        )
        assert out == 1
        episode = mem.episodes[-1]
        assert episode["project_id"] == project_fixture
        assert episode["record_type"] == "engineering_operation"
        assert episode["meta"]["tool"] == "sim"
        assert episode["meta"]["contract_id"] == "c1"

    def test_records_verified_result(self, project_fixture) -> None:
        mem = DummyMemory()
        em = EngineeringMemory(mem)
        any_time = datetime(2026, 6, 1, tzinfo=timezone.utc)
        base = SimulationResult(
            result_id="r1",
            task_id="t1",
            tool="sim",
            success=True,
            output={"x": 1.0},
            invoked_at=any_time,
        )
        v = SimulationVerifier().verify(base)
        out = em.record_result(project_fixture, v)
        assert out == 1
        episode = mem.episodes[-1]
        assert episode["record_type"] == "simulation_result"
        assert episode["meta"]["verdict"] == "pass"
        assert "verification_passed" in episode["tags"]
        assert episode["meta"]["passed_checks"] == ["structure", "success", "output_kind"]

    def test_records_failed_result_as_failed(self, project_fixture) -> None:
        mem = DummyMemory()
        em = EngineeringMemory(mem)
        any_time = datetime(2026, 6, 1, tzinfo=timezone.utc)
        base = SimulationResult(
            result_id="r1",
            task_id="t1",
            tool="sim",
            success=True,
            output=[1.0, 2.0],
            invoked_at=any_time,
        )
        v = SimulationVerifier().verify(base, expectation={"require_numeric_output": False})
        # Actually make one fail
        v2 = SimulationVerifier().verify(
            base,
            expectation={"require_mapping_output": True},
        )
        assert v2.failed_checks
        out = em.record_result(project_fixture, v2)
        episode = mem.episodes[-1]
        assert "verification_failed" in episode["tags"]
        assert episode["meta"]["verdict"] == "fail"


@pytest.fixture
def project_fixture() -> int:
    return 7
