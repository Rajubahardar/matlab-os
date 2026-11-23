"""End-to-end execution path for the unified agent ecosystem.

The path is fixed and reuses existing components at every stage::

    TaskContract
      -> Agent Registry          (Phase 12 authoritative registry)
      -> Capability Selection    (Phase 9 AgentSelector via SelectionAdapter)
      -> Lifecycle               (Phase 12 lifecycle manager)
      -> Control Plane           (Phase 10 LocalAIOSControlPlane: policy + audit)
      -> Existing Agent          (the registered MATLAB agent and its handlers)
      -> Validated Result        (typed ResultContract, never a raw throwable)

No second orchestration engine, model router, permission manager or scheduler is
introduced: the executor only composes the components above.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from matlab_os.integration.agent_ecosystem import contract as ec
from matlab_os.integration.agent_ecosystem.coordination import Coordination
from matlab_os.integration.agent_ecosystem.lifecycle import LifecycleManager
from matlab_os.integration.agent_ecosystem.memory_facade import MemoryFacade
from matlab_os.integration.agent_ecosystem.registry import Registry
from matlab_os.integration.agent_ecosystem.selection import SelectionAdapter, SelectionError
from matlab_os.phase9.agents.capability import AgentProfile
from matlab_os.phase9.agents.directory import AgentDirectory
from matlab_os.phase9.agents.selector import AgentSelector


class EcosystemExecutionError(ValueError):
    """The ecosystem cannot execute this task."""


class NoEligibleAgentError(EcosystemExecutionError):
    """Capability selection found no agent that satisfies the task."""


class AgentExecutionError(EcosystemExecutionError):
    """The selected agent failed to execute, or has no execution binding."""


class ResultVerificationError(EcosystemExecutionError):
    """The execution result did not pass contract validation."""


class EcosystemExecutor:
    """Runs one task through registry, selection, lifecycle and control plane."""

    def __init__(
        self,
        *,
        registry: Registry,
        lifecycle: LifecycleManager,
        control_plane: Any,
        memory: MemoryFacade | None = None,
        coordination: Coordination | None = None,
        directory: AgentDirectory | None = None,
        selection: SelectionAdapter | None = None,
        complexity: int = 4,
    ) -> None:
        self.registry = registry
        self.lifecycle = lifecycle
        self.control_plane = control_plane
        self.memory = memory
        self.coordination = coordination
        self.directory = directory if directory is not None else AgentDirectory()
        self.selection = (
            selection
            if selection is not None
            else SelectionAdapter(AgentSelector(self.directory))
        )
        self.complexity = complexity
        self._runners: dict[str, Callable[[ec.TaskContract, Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]] = {}

    # ------------------------------------------------------------------ #
    # Registration                                                      #
    # ------------------------------------------------------------------ #

    def register_agent(self, profile: AgentProfile) -> str:
        """Register an agent in the authoritative registry and the selector."""
        registration_id = self.registry.register(profile)
        self._sync_profile(profile)
        return registration_id

    def bind_runner(
        self,
        agent_id: str,
        runner: Callable[[ec.TaskContract, Mapping[str, Any], Mapping[str, Any]], dict[str, Any]],
    ) -> None:
        """Bind an execution callable for an agent that is not the MATLAB agent."""
        self._runners[agent_id] = runner

    def _sync_profile(self, profile: AgentProfile) -> None:
        capabilities = profile.capabilities
        if isinstance(capabilities, Mapping):
            capabilities = {
                name: (
                    capability
                    if hasattr(capability, "name")
                    else _capability(name, capability)
                )
                for name, capability in capabilities.items()
            }
        self.directory.register(
            AgentProfile(
                agent_id=profile.agent_id,
                display_name=profile.display_name,
                capabilities=capabilities,
                tools=frozenset(profile.tools),
                permission_scope=frozenset(profile.permission_scope),
                max_complexity=profile.max_complexity,
                max_concurrent_tasks=profile.max_concurrent_tasks,
                locality=profile.locality,
                status="available",
                enabled=True,
            )
        )

    # ------------------------------------------------------------------ #
    # Execution                                                         #
    # ------------------------------------------------------------------ #

    def execute(
        self,
        task: ec.TaskContract | Mapping[str, Any],
        *,
        required_capabilities: tuple[str, ...] | list[str] | None = None,
        required_tools: tuple[str, ...] | list[str] | None = None,
        tool: str | None = None,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = task if isinstance(task, ec.TaskContract) else ec.TaskContract.from_dict(task)

        capabilities = tuple(required_capabilities or task.required_capabilities)
        if not capabilities:
            raise EcosystemExecutionError(
                "a task must declare required_capabilities for capability selection."
            )

        candidate_ids = self._candidate_ids(capabilities)
        if not candidate_ids:
            raise NoEligibleAgentError(
                f"no registered agent declares capabilities {sorted(capabilities)}."
            )

        try:
            assignment = self.selection.select(
                candidate_ids,
                capabilities,
                required_tools=required_tools,
                complexity=self.complexity,
            )
        except SelectionError as exc:
            raise NoEligibleAgentError(str(exc)) from exc

        agent_id = assignment["assigned_agent"]
        self._ensure_ready(agent_id)
        dispatch = self.lifecycle.execute(agent_id, task)

        started = time.monotonic()
        try:
            execution = self._dispatch(agent_id, task, tool, arguments)
        except Exception as exc:  # a failing agent must not escape as a raw throwable
            duration = max(0.0, time.monotonic() - started)
            result = ec.ResultContract(
                task_id=task.task_id,
                agent_id=agent_id,
                status="failed",
                output={"agent": agent_id},
                errors=(f"{type(exc).__name__}: {exc}",),
                duration_seconds=duration,
                verification_status="failed",
                provenance=(agent_id,),
            )
            verified = self._validate(result)
            self._record(task, verified, dispatch)
            raise AgentExecutionError(str(exc)) from exc

        duration = max(0.0, time.monotonic() - started)
        result = ec.ResultContract(
            task_id=task.task_id,
            agent_id=agent_id,
            status=execution["status"],
            output=execution["output"],
            errors=tuple(execution.get("errors", ())),
            warnings=tuple(execution.get("warnings", ())),
            duration_seconds=duration,
            resource_usage={"executed_steps": execution["executed_steps"]},
            evidence_refs=execution.get("evidence_refs", ()),
            verification_status=execution["verification_status"],
            provenance=(agent_id,),
        )
        verified = self._validate(result)
        memory_experiment = self._record(task, verified, dispatch)

        return {
            "task_id": task.task_id,
            "agent_id": agent_id,
            "selection": assignment,
            "dispatch": dispatch,
            "status": verified.status,
            "verification_status": verified.verification_status,
            "result": verified.to_dict(),
            "trace": execution["trace"],
            "memory_experiment_id": memory_experiment,
        }

    # ------------------------------------------------------------------ #
    # Internals                                                         #
    # ------------------------------------------------------------------ #

    def _candidate_ids(self, capabilities: tuple[str, ...]) -> list[str]:
        candidates: set[str] | None = None
        for capability in capabilities:
            declared = set(self.registry.lookup_by_capability(capability))
            candidates = declared if candidates is None else (candidates & declared)
        return sorted(candidates or ())

    def _ensure_ready(self, agent_id: str) -> None:
        state = next(
            (entry["status"] for entry in self.registry.discover() if entry["agent_id"] == agent_id),
            None,
        )
        if state is None:
            raise EcosystemExecutionError(f"agent {agent_id!r} is not registered.")
        if state == "registered":
            self.lifecycle.initialize(agent_id)
            state = "initialized"
        if state == "initialized":
            self.lifecycle.ready(agent_id)

    def _dispatch(
        self,
        agent_id: str,
        task: ec.TaskContract,
        tool: str | None,
        arguments: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        runner = self._runners.get(agent_id)
        if runner is not None:
            payload = runner(task, tool, arguments or {})
            trace = list(payload.get("trace", ()))
            return {
                "status": str(payload.get("status", "completed")).lower(),
                "output": dict(payload.get("output", {})),
                "trace": trace,
                "executed_steps": int(payload.get("executed_steps", len(trace))),
                "verification_status": payload.get("verification_status", "verified"),
                "warnings": payload.get("warnings", ()),
                "evidence_refs": payload.get("evidence_refs", ()),
            }

        # The MATLAB agent is executed by the existing Phase 10 control plane,
        # which owns policy, approval, audit and the MATLAB transport.
        outcome = self.control_plane.run(
            task.objective,
            matlab_tool=tool,
            matlab_arguments=dict(arguments or {}),
            requested_by=task.actor,
            request_id=task.task_id,
        )
        trace = self.control_plane.trace()
        executed = [step for step in trace if step.get("executed")]

        raw_status = getattr(outcome, "status", "completed")
        status = str(getattr(raw_status, "value", raw_status)).lower()

        if status != "completed":
            errors = tuple(
                str(step["error"]) for step in trace if step.get("error")
            )
            if not errors:
                errors = (
                    str(getattr(outcome, "error", "") or "control plane did not complete"),
                )
            return {
                "status": "failed",
                "output": {"control_plane": getattr(outcome, "to_dict", lambda: {})()},
                "trace": trace,
                "executed_steps": len(executed),
                "verification_status": "failed",
                "errors": errors,
                "warnings": (str(getattr(outcome, "error", "") or "control plane did not complete"),),
            }

        return {
            "status": "completed",
            "output": {
                "tool": tool,
                "steps": trace,
                "control_plane_status": status,
            },
            "trace": trace,
            "executed_steps": len(executed),
            "errors": (),
            # A completed control-plane run without a real executed step is not
            # evidence that the agent acted.
            "verification_status": "verified" if executed else "unverified",
        }

    def _validate(self, result: ec.ResultContract) -> ec.ResultContract:
        try:
            verified = ec.validate_result(result.to_dict())
        except Exception as exc:
            raise ResultVerificationError(str(exc)) from exc
        if verified.is_failure and verified.status != "failed":
            raise ResultVerificationError(f"agent returned a failure status {verified.status!r}.")
        return verified

    def _record(
        self,
        task: ec.TaskContract,
        result: ec.ResultContract,
        dispatch: Mapping[str, Any],
    ) -> str | None:
        experiment_id: str | None = None
        if self.memory is not None:
            try:
                written = self.memory.write_task_context(
                    task.task_id,
                    task.actor,
                    {
                        "objective": task.objective,
                        "agent_id": result.agent_id,
                        "status": result.status,
                        "verification_status": result.verification_status,
                        "dispatch_id": dispatch.get("dispatch_id"),
                    },
                )
                experiment_id = written.get("experiment_id")
            except Exception:
                # A previously written context for the same task is a conflict,
                # not a reason to lose the execution result.
                experiment_id = None
        if self.coordination is not None:
            # A failed result is never a handoff, but it must still leave a
            # provenance record: the run happened and it failed.
            if result.is_failure:
                self.coordination.record_failure(task.task_id, result.to_dict())
            else:
                self.coordination.register_result(task.task_id, result.to_dict())
        return experiment_id


def _capability(name: str, value: Any) -> Any:
    from matlab_os.phase9.agents.capability import AgentCapability

    tools = value.get("tools", ()) if isinstance(value, Mapping) else ()
    return AgentCapability(name=name, tools=tuple(tools))
