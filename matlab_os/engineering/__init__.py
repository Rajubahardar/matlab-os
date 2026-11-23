"""Phase 15: MATLAB / Simulink engineering intelligence.

This module adds the engineering layer on top of the existing agent ecosystem,
approval policy, and memory surface:

* task contracts: a constrained, versioned description of what a MATLAB/Simulink
  operation is supposed to achieve, what it may touch, and what output it must
  produce;
* simulation-result verification: a result object with a verdict and concrete
  pass/fail signals (exit status, output shape, result data checks);
* engineering memory: a thin bridge that records engineering operations and results
  as memories so later steps can reason about the system's engineering history.

This is intentionally a thin verification/record layer. It does **not** replace
the MATLAB MCP transport, the approval policy, or the resource governor; it
consumes them. Real MATLAB/Simulink execution still happens through the existing
control plane / MCP client only when the system is wired for it and the user has
approvals in place.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class EngineeringError(Exception):
    """An engineering-level precondition or verification failure."""


class ContractValidationError(EngineeringError):
    """A task contract failed basic validation."""


class VerificationError(EngineeringError):
    """A simulation result fails its verification checks."""


_TASK_TYPE_CHOICES = frozenset({"matlab_script", "simulink_model", "data_export", "report"})


class ExecutionTarget:
    """Where a MATLAB/Simulink operation is expected to run.

    This is intentionally describe-only: it names the target and the workspace it
    should touch, but it does not connect or execute anything.
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        task_id: str,
        tool: str,
        target: Mapping[str, Any] | None = None,
        target_kind: str = "file",
    ) -> None:
        if not isinstance(task_id, str) or not task_id.strip():
            raise EngineeringError("task_id must be a non-empty string.")
        if not isinstance(tool, str) or not tool.strip():
            raise EngineeringError("tool must be a non-empty string.")
        if not _workspace_is_absolute(workspace_root):
            raise EngineeringError("workspace_root must be an absolute Path.")

        self.workspace_root = workspace_root
        self.task_id = task_id
        self.tool = tool
        self.target = dict(target or {})
        self.target_kind = target_kind
        self.created_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return (
            f"ExecutionTarget(workspace={self.workspace_root}, task={self.task_id!r}, "
            f"tool={self.tool!r})"
        )


def _workspace_is_absolute(workspace_root: Any) -> bool:
    return isinstance(workspace_root, Path) and workspace_root.is_absolute()


class TaskContract:
    """A constrained description of one engineering operation.

    In later phases this is the shape that planning, approval, and execution all
    agree on for a MATLAB/Simulink task: what the task is, what kind of operation
    it is, what it may touch, and what output it must produce.
    """

    def __init__(
        self,
        *,
        contract_id: str,
        task_id: str,
        objective: str,
        operation: str,
        tool: str,
        target: Mapping[str, Any] | None = None,
        target_kind: str = "file",
        output_expectation: str | None = None,
        required_capabilities: list[str] | None = None,
        workspace_root: Path | None = None,
        version: int = 1,
        created_by: str | None = None,
        created_at: datetime | None = None,
    ) -> None:
        if not contract_id or not isinstance(contract_id, str):
            raise ContractValidationError("contract_id must be a non-empty string.")
        if not task_id or not isinstance(task_id, str):
            raise ContractValidationError("task_id must be a non-empty string.")
        if not objective or not isinstance(objective, str):
            raise ContractValidationError("objective must be a non-empty string.")
        if not operation or not isinstance(operation, str):
            raise ContractValidationError("operation must be a non-empty string.")
        if not tool or not isinstance(tool, str):
            raise ContractValidationError("tool must be a non-empty string.")
        if workspace_root is None:
            raise ContractValidationError("contracts must name an absolute workspace_root.")
        if not isinstance(workspace_root, Path) or not workspace_root.is_absolute():
            raise ContractValidationError("workspace_root must be an absolute Path.")
        if target_kind not in _TASK_TYPE_CHOICES:
            raise ContractValidationError(
                f"target_kind must be one of {_TASK_TYPE_CHOICES!r}, got {target_kind!r}."
            )
        if versions_not_strictly_increasing(version):
            raise ContractValidationError("version must be >= 1.")

        self.contract_id = contract_id
        self.task_id = task_id
        self.objective = objective
        self.operation = operation
        self.tool = tool
        self.target = dict(target or {})
        self.target_kind = target_kind
        self.output_expectation = output_expectation
        self.required_capabilities = list(required_capabilities or [])
        self.workspace_root = workspace_root
        self.version = version
        self.created_by = created_by or "operator"
        self.created_at = created_at or datetime.now(timezone.utc)

    def validated(self) -> "TaskContract":
        """Return self after re-checking the contract's invariants."""
        # Re-validate the same invariants that __init__ enforces.
        if not _workspace_is_absolute(self.workspace_root):
            raise ContractValidationError("workspace_root must be an absolute Path.")
        if self.target_kind not in _TASK_TYPE_CHOICES:
            raise ContractValidationError(
                f"target_kind must be one of {_TASK_TYPE_CHOICES!r}, got {self.target_kind!r}."
            )
        if not isinstance(self.output_expectation, (str, type(None))):
            raise ContractValidationError(
                "output_expectation must be a string or None."
            )
        return self

    def as_execution_target(self) -> ExecutionTarget:
        """Build an execution target describing where this contract is meant to run."""
        return ExecutionTarget(
            workspace_root=self.workspace_root,
            task_id=self.task_id,
            tool=self.tool,
            target=self.target,
            target_kind=self.target_kind,
        )

    def snapshot(self) -> Mapping[str, Any]:
        return {
            "contract_id": self.contract_id,
            "task_id": self.task_id,
            "objective": self.objective,
            "operation": self.operation,
            "tool": self.tool,
            "target": self.target,
            "target_kind": self.target_kind,
            "output_expectation": self.output_expectation,
            "required_capabilities": self.required_capabilities,
            "workspace_root": str(self.workspace_root),
            "version": self.version,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }


def versions_not_strictly_increasing(version: int) -> bool:
    return not (isinstance(version, int) and version >= 1)


class SimulationResult:
    """The outcome of one MATLAB/Simulink simulation or analysis run.

    This is the object that verification operates on. A result is not automatically
    "good" just because the MCP transport returned something; it must also pass the
    expectation checks that engineering verification defines for the task.
    """

    def __init__(
        self,
        *,
        result_id: str,
        task_id: str,
        tool: str,
        success: bool,
        output: Any | None = None,
        error: str | None = None,
        invoked_at: datetime | None = None,
        meta: Mapping[str, Any] | None = None,
        stdout_text: str | None = None,
        signals: Mapping[str, Any] | None = None,
    ) -> None:
        if not result_id or not isinstance(result_id, str):
            raise EngineeringError("result_id must be a non-empty string.")
        if not task_id or not isinstance(task_id, str):
            raise EngineeringError("task_id must be a non-empty string.")
        if not tool or not isinstance(tool, str):
            raise EngineeringError("tool must be a non-empty string.")

        self.result_id = result_id
        self.task_id = task_id
        self.tool = tool
        self.success = success
        self.output = output
        self.error = error
        self.invoked_at = invoked_at or datetime.now(timezone.utc)
        self.meta = dict(meta or {})
        self.stdout_text = stdout_text or ""
        self.signals = dict(signals or {})

    def is_successful(self) -> bool:
        return bool(self.success)

    def _expected_output_keys(self, expectation: Mapping[str, Any]) -> list[str]:
        raw = expectation.get("output_keys")
        if isinstance(raw, (list, tuple)):
            return [str(k) for k in raw]
        return []


class SimulationResultVerified(SimulationResult):
    """A simulation result that has passed verification checks."""

    def __init__(
        self,
        result: SimulationResult,
        *,
        passed_checks: list[str],
        failed_checks: list[str] | None = None,
    ) -> None:
        super().__init__(
            result_id=result.result_id,
            task_id=result.task_id,
            tool=result.tool,
            success=result.success and not failed_checks,
            output=result.output,
            error=result.error,
            invoked_at=result.invoked_at,
            meta=dict(result.meta),
            stdout_text=result.stdout_text,
            signals=dict(result.signals),
        )
        self.passed_checks = list(passed_checks)
        self.failed_checks = list(failed_checks or [])


class SimulationVerifier:
    """Verify that a simulation result matches its task expectation.

    Verification is intentionally lightweight and deterministic: it checks that the
    result is structurally usable and that any declared output expectations hold.
    This is the engineering "does the result look right?" gate, not a full scientific
    validation pass.
    """

    def __init__(self) -> None:
        self._decision_log: list[dict[str, Any]] = []

    def verify(
        self,
        result: SimulationResult,
        *,
        expectation: Mapping[str, Any] | None = None,
        checks: list[str] | None = None,
    ) -> SimulationResultVerified:
        if not isinstance(result, SimulationResult):
            raise VerificationError("verify() requires a SimulationResult.")

        expectation = dict(expectation or {})
        checks = list(checks or [])

        # Default checks
        if not checks:
            checks = ["structure", "success", "output_kind"]

        passed: list[str] = []
        failed: list[str] = []

        if "structure" in checks:
            if _check_structure(result, expectation):
                passed.append("structure")
            else:
                failed.append("structure")

        if "success" in checks:
            if _check_success(result, expectation):
                passed.append("success")
            else:
                failed.append("success")

        if "output_kind" in checks:
            if _check_output_kind(result, expectation):
                passed.append("output_kind")
            else:
                failed.append("output_kind")

        decision = {
            "result_id": result.result_id,
            "task_id": result.task_id,
            "checks": checks,
            "passed": passed,
            "failed": failed,
            "verdict": "pass" if not failed else "fail",
        }
        self._decision_log.append(decision)
        return SimulationResultVerified(result, passed_checks=passed, failed_checks=failed)

    def checks_passed(self) -> int:
        return sum(1 for d in self._decision_log if d["verdict"] == "pass")

    def checks_total(self) -> int:
        return len(self._decision_log)


def _check_structure(result: SimulationResult, expectation: Mapping[str, Any]) -> bool:
    if result.success is False:
        return False
    if not _is_usable_output(result.output):
        return False
    if expectation.get("require_nonempty_output") and len(result.stdout_text or "") == 0:
        return False
    return True


def _check_success(result: SimulationResult, expectation: Mapping[str, Any]) -> bool:
    return bool(result.success)


def _check_output_kind(result: SimulationResult, expectation: Mapping[str, Any]) -> bool:
    if expectation.get("require_numeric_output") and not _is_numeric_like(result.output):
        return False
    if expectation.get("require_mapping_output") and not isinstance(result.output, Mapping):
        return False
    if expectation.get("require_list_output") and not isinstance(result.output, list):
        return False
    output_keys = _expected_output_keys(expectation)
    if output_keys:
        if not isinstance(result.output, Mapping):
            return False
        if not all(k in result.output for k in output_keys):
            return False
    return True


def _is_usable_output(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes, int, float, list, tuple, Mapping, set)):
        return True
    try:
        return bool(value)
    except Exception:
        return False


def _is_numeric_like(value: Any) -> bool:
    if isinstance(value, (int, float, complex)):
        return True
    if isinstance(value, str):
        try:
            float(value)
            return True
        except ValueError:
            return False
    if isinstance(value, (list, tuple)):
        return all(_is_numeric_like(v) for v in value)
    if isinstance(value, Mapping):
        return all(_is_numeric_like(v) for v in value.values())
    return False


def _expected_output_keys(expectation: Mapping[str, Any]) -> list[str]:
    raw = expectation.get("output_keys")
    if isinstance(raw, (list, tuple)):
        return [str(k) for k in raw]
    return []



class EngineeringMemory:
    """Bridge: record engineering operations and results as memories.

    Uses the existing MemoryService facade rather than introducing a second store.
    """

    def __init__(self, memory: Any) -> None:
        if not hasattr(memory, "store_episode") or not callable(memory.store_episode):
            raise EngineeringError("EngineeringMemory requires a MemoryService-like object.")
        if not hasattr(memory, "store_episode") or not callable(memory.store_episode):
            raise EngineeringError("EngineeringMemory requires a MemoryService-like object.")
        self.memory = memory

    def record_operation(
        self,
        project_id: int,
        contract_snapshot: Mapping[str, Any],
        operation: str,
        tool: str,
        outcome: Mapping[str, Any],
        *,
        reason: str | None = None,
    ) -> int:
        if not isinstance(project_id, int) or project_id <= 0:
            raise EngineeringError("project_id must be a positive integer.")

        record = {
            "operation": operation,
            "tool": tool,
            "contract_id": contract_snapshot.get("contract_id"),
            "task_id": contract_snapshot.get("task_id"),
            "outcome": dict(outcome),
            "reason": reason,
            "outcome_type": "engineering_operation",
        }
        return self.memory.store_episode(
            project_id=project_id,
            content=f"[engineering] {operation} via {tool}",
            record_type="engineering_operation",
            tags={"engineering", "operation", tool},
            confidence=0.85,
            meta=record,
        )

    def record_result(
        self,
        project_id: int,
        result: SimulationResultVerified,
        expectation: Mapping[str, Any] | None = None,
    ) -> int:
        if not isinstance(project_id, int) or project_id <= 0:
            raise EngineeringError("project_id must be a positive integer.")

        expectation = dict(expectation or {})
        record = {
            "result_id": result.result_id,
            "task_id": result.task_id,
            "tool": result.tool,
            "verdict": "pass" if not result.failed_checks else "fail",
            "passed_checks": result.passed_checks,
            "failed_checks": result.failed_checks,
            "output": result.output,
            "expectation": expectation,
            "outcome_type": "simulation_result",
        }
        tags = {"engineering", "simulation", result.tool, "result"}
        if result.failed_checks:
            tags.add("verification_failed")
        else:
            tags.add("verification_passed")

        return self.memory.store_episode(
            project_id=project_id,
            content=f"[engineering] simulation result {result.result_id!r} for {result.task_id!r}",
            record_type="simulation_result",
            tags=tags,
            confidence=0.7 if result.failed_checks else 0.9,
            meta=record,
        )
