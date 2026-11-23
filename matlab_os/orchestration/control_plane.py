"""Phase 10: the unified Local AI OS control plane.

One orchestration path:

    user request
      -> planner            (Phase 9 ``TaskPlanner``)
      -> permission policy  (Phase 9 ``PolicyEngine``: exact capability grants)
      -> agent selection    (Phase 9 ``AgentSelector`` + ``AgentDirectory``)
      -> resource admission (Phase 9 ``ResourceGovernor``)
      -> execution          (Phase 9 ``PlanExecutor`` + handlers)
      -> workspace / tool   (Phase 9 ``WorkspaceManager``, MATLAB read-only policy)
      -> validation + audit (Phase 9 ``AuditLog``)

The control plane is the single authority for authorisation: a handler cannot
reach MATLAB unless :class:`MatlabToolPolicy` approves the operation, and the
policy is consulted on every step, not once per task.

A plan may contain several steps (analysis, implementation, verification...).
One user request maps to at most ONE MATLAB operation, guarded by a run-scoped
marker, so a mutating tool can never be replayed once per planning step.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from matlab_os.integration.matlab_approval import (
    ApprovalAuthority,
    ApprovalError,
    MutationRequest,
    approval_target,
)
from matlab_os.integration.matlab_policy import MatlabToolPolicy
from matlab_os.phase9.agents.capability import AgentCapability, AgentProfile
from matlab_os.phase9.config import Phase9Config
from matlab_os.phase9.errors import Phase9Error
from matlab_os.phase9.runtime.orchestrator import Phase9Runtime

#: Capabilities the MATLAB agent declares.  These are the canonical Phase 9
#: capability names, so the planner's vocabulary and the registry agree.
MATLAB_CAPABILITIES: tuple[str, ...] = (
    "analysis",
    "debugging",
    "design",
    "documentation",
    "implementation",
    "optimization",
    "research",
    "simulation",
    "verification",
)

#: Context keys understood by the MATLAB handlers.
TOOL_KEY = "matlab_tool"
ARGUMENTS_KEY = "matlab_arguments"
ACTOR_KEY = "matlab_actor"
REQUEST_KEY = "matlab_request_id"
APPROVAL_KEY = "matlab_approval"

#: A transport is any callable ``(tool_name, arguments) -> Any``.  Production
#: passes :func:`matlab_mcp_transport`; tests pass a recorder.
MatlabTransport = Callable[[str, Mapping[str, Any]], Any]

ALREADY_EXECUTED = "MATLAB operation already executed for this request."

MCP_ERROR_FALLBACK = "MATLAB MCP tool reported an error."


class MatlabToolError(Phase9Error):
    """A MATLAB MCP tool was invoked but did not return a usable result.

    The transport ran, but the tool reported an error or returned a payload
    that cannot be a successful MCP result.  This is permanent on purpose: the
    re-planner aborts instead of retrying an operation whose outcome is
    already known, so a failed call can never be replayed.
    """

    code = "matlab_tool_error"


def mcp_error_message(output: Any) -> Optional[str]:
    """Return a failure message when an MCP tool result is not a success.

    A successful result is a mapping shaped like
    ``{"error": False, "content": [{"type": "text", "text": ...}]}``.  A
    tool-level failure sets ``error`` / ``isError`` / ``is_error`` and carries
    the reason in ``text`` or in ``content`` text items.  Anything that is not
    a mapping cannot be a successful MCP result, so it fails closed too.
    """

    if not isinstance(output, Mapping):
        return (
            "unexpected MCP response type "
            f"{type(output).__name__}: expected a mapping."
        )

    flag = output.get("error", output.get("isError", output.get("is_error")))
    if not flag:
        return None

    texts: list[str] = []
    content = output.get("content")
    if isinstance(content, (list, tuple)):
        for item in content:
            if isinstance(item, Mapping) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
    for key in ("text", "message"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value.strip())
    return "; ".join(dict.fromkeys(texts)) or MCP_ERROR_FALLBACK


def matlab_mcp_transport(client: Any) -> MatlabTransport:
    """Adapt a live :class:`MCPClient` into a control-plane transport.

    The client itself requires ``approved=True``; that flag is set here only
    *after* :class:`MatlabToolPolicy` has authorised the operation, so the
    policy — not the caller — is the decision point.
    """

    def call(tool_name: str, arguments: Mapping[str, Any]) -> Any:
        return client.call_tool(tool_name, dict(arguments), approved=True)

    return call


class LocalAIOSControlPlane:
    """Single entry point for planning, authorising and executing work."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        state_root: str | Path | None = None,
        transport: MatlabTransport | None = None,
        policy: MatlabToolPolicy | None = None,
        approval: ApprovalAuthority | None = None,
        max_retries: int = 1,
        config: Phase9Config | None = None,
        monitor: Any | None = None,
    ) -> None:
        root = Path(workspace_root).resolve()
        root.mkdir(parents=True, exist_ok=True)

        self.workspace_root = root
        self.policy = policy if policy is not None else MatlabToolPolicy()
        self.transport = transport
        self.max_retries = max_retries

        # Callers may pin resource/security budgets explicitly (tests do this so
        # admission does not depend on the host's instantaneous CPU load).
        config = config if config is not None else Phase9Config.default(str(root))
        self.runtime: Phase9Runtime = Phase9Runtime.build(
            config, state_root=state_root, monitor=monitor
        )

        self._trace: list[dict[str, Any]] = []
        self._run_state: dict[str, Any] = {}

        self.approval = approval or ApprovalAuthority(
            policy=self.policy, audit=self._audit_approval
        )

        self._register_matlab_agent()
        self._register_handlers()

    # -- composition ------------------------------------------------------- #
    def _register_matlab_agent(self) -> None:
        profile = AgentProfile(
            agent_id="matlab",
            display_name="MATLAB Expert Agent",
            capabilities={
                name: AgentCapability(
                    name=name,
                    # The Phase 9 planner emits steps that require the
                    # workspace and model tools, so the profile must declare
                    # them or selection rejects every step.
                    tools=("workspace", "model", "matlab_mcp"),
                    max_complexity=4,
                    description=f"MATLAB/Simulink {name}",
                )
                for name in MATLAB_CAPABILITIES
            },
            tools=frozenset({"workspace", "model", "matlab_mcp"}),
            permission_scope=frozenset(
                {"workspace.read", "workspace.write", "model.invoke"}
            ),
            max_complexity=4,
            max_concurrent_tasks=1,
            locality="local",
        )
        self.runtime.register_agent(profile)

    def _register_handlers(self) -> None:
        for capability in MATLAB_CAPABILITIES:
            self.runtime.register_handler(
                capability, self._make_handler(capability)
            )

    def _make_handler(
        self, capability: str
    ) -> Callable[[Any, Mapping[str, Any]], Any]:
        def handler(step: Any, context: Mapping[str, Any]) -> dict[str, Any]:
            return self._run_matlab_step(capability, step, context)

        return handler

    def _audit_approval(self, event: dict[str, Any]) -> None:
        """Route approval events into the Phase 9 audit log."""

        details = {k: v for k, v in event.items() if k != "action"}
        self.runtime.audit.record(
            action=event.get("action", "matlab.approval"),
            actor=str(details.get("approver") or "approval-authority"),
            outcome="denied" if "refused" in event.get("action", "") else "allowed",
            details=details,
        )

    # -- MATLAB read-only execution ---------------------------------------- #
    def _record(self, record: dict[str, Any]) -> dict[str, Any]:
        self._trace.append(record)
        return record

    def _run_matlab_step(
        self,
        capability: str,
        step: Any,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        tool_name = context.get(TOOL_KEY)
        arguments = dict(context.get(ARGUMENTS_KEY) or {})
        step_id = getattr(step, "step_id", "")

        if not tool_name:
            # Planning-only step: nothing is executed, nothing is authorised.
            return self._record(
                {
                    "capability": capability,
                    "step_id": step_id,
                    "tool": None,
                    "executed": False,
                    "reason": "No MATLAB tool was requested for this step.",
                }
            )

        # One request executes the MATLAB operation at most once, no matter how
        # many planning steps it expands into.  This runs before authorisation
        # so a completed operation is never re-approved: a single-use ticket
        # would otherwise be refused as a replay by the next planning step.
        state = self._run_state
        if state.get("executed") and state.get("tool") == tool_name:
            return self._record(
                {
                    "capability": capability,
                    "step_id": step_id,
                    "tool": tool_name,
                    "executed": False,
                    "reason": ALREADY_EXECUTED,
                }
            )

        decision = self.policy.decide(tool_name, arguments)
        self.runtime.audit.record(
            action="matlab.authorisation",
            actor="control-plane",
            outcome="allowed" if decision["allowed"] else "denied",
            details={
                "tool": tool_name,
                "operation": decision["operation"],
                "capability": capability,
                "step_id": step_id,
                "reason": decision["reason"],
            },
        )

        if not decision["allowed"]:
            raise PermissionError(f"{tool_name} refused: {decision['reason']}")

        # A mutating or code-executing operation additionally needs a
        # single-use approval bound to this exact tool, arguments, target,
        # actor and request.  Read-only work needs none.
        if decision.get("requires_approval"):
            request = MutationRequest(
                tool=tool_name,
                arguments=arguments,
                target=approval_target(arguments, self.workspace_root),
                actor=str(context.get(ACTOR_KEY) or "operator"),
                request_id=str(context.get(REQUEST_KEY) or ""),
            )
            self.approval.authorize(request, context.get(APPROVAL_KEY))

        if self.transport is None:
            return self._record(
                {
                    "capability": capability,
                    "step_id": step_id,
                    "tool": tool_name,
                    "operation": decision["operation"],
                    "executed": False,
                    "reason": "No MATLAB transport configured.",
                }
            )

        try:
            output = self.transport(tool_name, arguments)
        except Exception as exc:
            # The transport boundary failed: the call may or may not have
            # reached MATLAB.  Mark the run as spent so a retry can never
            # replay it, then fail with the real cause preserved.
            state["executed"] = True
            state["tool"] = tool_name
            state["output"] = None
            message = (
                f"{tool_name} transport failed: {type(exc).__name__}: {exc}"
            )
            self._record(
                {
                    "capability": capability,
                    "step_id": step_id,
                    "tool": tool_name,
                    "operation": decision["operation"],
                    "executed": False,
                    "invoked": True,
                    "failed": True,
                    "error": message,
                }
            )
            raise MatlabToolError(
                message,
                tool=tool_name,
                operation=decision["operation"],
                step_id=step_id,
            ) from exc

        # A successful transport call is not a successful tool result.  An MCP
        # error payload must fail the step; it must never be recorded as
        # executed output, or a failed MATLAB call would look verified.
        failure = mcp_error_message(output)
        if failure is not None:
            state["executed"] = True
            state["tool"] = tool_name
            state["output"] = output
            message = f"{tool_name} failed: {failure}"
            self._record(
                {
                    "capability": capability,
                    "step_id": step_id,
                    "tool": tool_name,
                    "operation": decision["operation"],
                    "executed": False,
                    "invoked": True,
                    "failed": True,
                    "error": message,
                    "output": output,
                }
            )
            raise MatlabToolError(
                message,
                tool=tool_name,
                operation=decision["operation"],
                step_id=step_id,
            )

        state["executed"] = True
        state["tool"] = tool_name
        state["output"] = output

        return self._record(
            {
                "capability": capability,
                "step_id": step_id,
                "tool": tool_name,
                "operation": decision["operation"],
                "executed": True,
                "failed": False,
                "output": output,
            }
        )

    # -- public API -------------------------------------------------------- #
    def run(
        self,
        task: str,
        *,
        matlab_tool: str | None = None,
        matlab_arguments: Mapping[str, Any] | None = None,
        requested_by: str = "operator",
        request_id: str | None = None,
        approval_ticket: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> Any:
        """Plan, authorise and execute one user request."""

        context: dict[str, Any] = {
            ACTOR_KEY: requested_by,
            REQUEST_KEY: request_id or uuid.uuid4().hex,
        }
        if matlab_tool:
            context[TOOL_KEY] = matlab_tool
        if matlab_arguments:
            context[ARGUMENTS_KEY] = dict(matlab_arguments)
        if approval_ticket:
            context[APPROVAL_KEY] = approval_ticket

        self._run_state = {"executed": False, "tool": None, "output": None}

        return self.runtime.run(
            task,
            context=context,
            requested_by=requested_by,
            on_event=on_event,
        )

    def authorize(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Expose the policy decision without executing anything."""

        return self.policy.decide(tool_name, arguments)

    def trace(self) -> list[dict[str, Any]]:
        """Per-step operational trace of the most recent run."""

        return [dict(record) for record in self._trace]

    def health(self) -> dict[str, Any]:
        """Local, network-free status for operators and tests."""

        runtime_health = self.runtime.health()
        profiles = self.runtime.directory.all()

        return {
            "control_plane": "local-ai-os",
            "workspace_root": str(self.workspace_root),
            "matlab_transport": "configured" if self.transport else "absent",
            "matlab_policy": {
                "allow_mutating": self.policy.allow_mutating,
                "allow_code_execution": self.policy.allow_code_execution,
                "posture": "read-only"
                if not (self.policy.allow_mutating or self.policy.allow_code_execution)
                else "extended",
            },
            "agents": [getattr(profile, "agent_id", "?") for profile in profiles],
            "runtime": runtime_health,
        }


def build_control_plane(
    workspace_root: str | Path,
    *,
    state_root: str | Path | None = None,
    transport: MatlabTransport | None = None,
    allow_mutating: bool = False,
    allow_code_execution: bool = False,
    config: Phase9Config | None = None,
    monitor: Any | None = None,
) -> LocalAIOSControlPlane:
    """Factory used by the CLI/tests so posture is explicit at the edge.

    ``monitor`` is an optional resource-telemetry seam for tests.  When supplied
    it is passed directly to the runtime so admission can be made deterministic
    without changing the production governor.
    """

    policy = MatlabToolPolicy(
        allow_mutating=allow_mutating,
        allow_code_execution=allow_code_execution,
    )
    return LocalAIOSControlPlane(
        workspace_root=workspace_root,
        state_root=state_root,
        transport=transport,
        policy=policy,
        config=config,
        monitor=monitor,
    )
