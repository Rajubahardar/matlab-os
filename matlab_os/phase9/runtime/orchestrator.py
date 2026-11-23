"""Phase 9 runtime: one facade that wires every guarded layer together.

Wiring (top to bottom):

    Phase9Runtime
      â”œâ”€â”€ AuditLog              hash-chained, redacting, outside the sandbox
      â”œâ”€â”€ ApprovalGate          payload-bound single-use human approvals
      â”œâ”€â”€ PathGuard Ã—2          sandbox guard (blocks .phase9_backups) + backup guard
      â”œâ”€â”€ BackupStore           versioned copies + manifest + hash verification
      â”œâ”€â”€ WorkspaceManager      validated / approved / audited file operations
      â”œâ”€â”€ ResourceMonitor       stdlib CPU + RAM telemetry
      â”œâ”€â”€ ResourceGovernor      admission + concurrency + subprocess ceilings
      â”œâ”€â”€ ProviderRegistry      optional DeepSeek, local Ollama, mock
      â”œâ”€â”€ AgentDirectory        registered agent profiles and live status
      â”œâ”€â”€ AgentSelector         capability / tool / permission / capacity scoring
      â”œâ”€â”€ CollaborationCoordinator  depth, cycle, width, message and timeout budgets
      â”œâ”€â”€ TaskPlanner           heuristic + strictly validated model assistance
      â”œâ”€â”€ Replanner             bounded failure recovery
      â””â”€â”€ PlanExecutor          dependency-ordered execution with all hooks

There is exactly one path to execute work, and it passes through selection,
authorisation, admission, execution and audit.  Registration is the explicit
authorisation act: an agent is granted exactly the capabilities its profile
declares, and the grant list is written to the audit log.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from ..audit import AuditLog, NullAuditLog
from ..config import Phase9Config
from ..errors import (
    AgentSelectionError,
    ConfigurationError,
    PermissionDeniedError,
    Phase9Error,
    ProviderUnavailableError,
    WorkspaceError,
)
from ..planning.executor import ExecutionResult, PlanExecutor, StepHandler
from ..planning.model import ExecutionPlan, PlanStep
from ..planning.planner import TaskPlanner
from ..planning.replanner import Replanner
from ..security.approval import ApprovalGate
from ..security.paths import PathGuard
from ..security.policy import (
    CAPABILITY_PERMISSIONS,
    PHASE9_OPERATION,
    PHASE9_SKILL,
    PolicyEngine,
    required_permissions,
)
from ..security.redaction import redact
from ..workspace.backup import BackupStore
from ..workspace.operations import WorkspaceManager

BACKUP_DIRECTORY = ".phase9_backups"


class StepHandlerRegistry:
    """Explicit capability â†’ handler mapping. No implicit handlers exist."""

    def __init__(self) -> None:
        self._handlers: dict[str, StepHandler] = {}

    def register(self, capability: str, handler: StepHandler) -> None:
        if not isinstance(capability, str) or not capability.strip():
            raise ValueError("capability must be a non-empty string.")
        if not callable(handler):
            raise TypeError("handler must be callable.")
        self._handlers[capability.strip()] = handler

    def unregister(self, capability: str) -> bool:
        return self._handlers.pop(capability, None) is not None

    def get_handler(self, capability: str) -> Optional[StepHandler]:
        return self._handlers.get(capability)

    def capabilities(self) -> list[str]:
        return sorted(self._handlers)


@dataclass
class Submission:
    submission_id: str
    task: str
    plan: ExecutionPlan
    requested_by: str
    created_at: float
    status: str = "PLANNED"
    context: dict[str, Any] = field(default_factory=dict)
    result: Optional[ExecutionResult] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "task": self.task,
            "plan_id": self.plan.plan_id,
            "status": self.status,
            "requested_by": self.requested_by,
            "created_at": self.created_at,
            "progress": self.plan.progress(),
            "result": self.result.to_dict() if self.result else None,
        }


class Phase9Runtime:
    """Composition root for the Phase 9 staging implementation."""

    def __init__(  # use :meth:`build` instead of constructing directly
        self,
        *,
        config: Phase9Config,
        audit: AuditLog,
        approval_gate: ApprovalGate,
        sandbox_guard: PathGuard,
        backup_guard: PathGuard,
        backups: BackupStore,
        workspace: WorkspaceManager,
        monitor: Any,
        governor: Any,
        providers: Any,
        directory: Any,
        selector: Any,
        coordinator: Any,
        planner: TaskPlanner,
        replanner: Replanner,
        handlers: StepHandlerRegistry,
        executor: PlanExecutor,
        policy: PolicyEngine,
        state_root: Path,
        clock=time.time,
    ) -> None:
        self.config = config
        self.audit = audit
        self.approvals = approval_gate
        self.guard = sandbox_guard
        self.backup_guard = backup_guard
        self.backups = backups
        self.workspace = workspace
        self.monitor = monitor
        self.governor = governor
        self.providers = providers
        self.directory = directory
        self.selector = selector
        self.coordinator = coordinator
        self.planner = planner
        self.replanner = replanner
        self.handlers = handlers
        self.executor = executor
        self.policy = policy
        self.state_root = Path(state_root)
        self._clock = clock
        self._submissions: dict[str, Submission] = {}

    # -- construction ------------------------------------------------------ #
    @classmethod
    def build(
        cls,
        config: Phase9Config,
        *,
        state_root: Optional[str | Path] = None,
        transport: Any | None = None,
        monitor: Any | None = None,
        clock=time.time,
    ) -> "Phase9Runtime":
        """Compose a runtime.

        ``transport`` and ``monitor`` are dependency-injection seams.  Tests
        inject a scripted transport and a deterministic telemetry source so
        results do not depend on the host's instantaneous CPU/RAM readings;
        production runs leave them as ``None`` and sample the real host.
        """
        if not isinstance(config, Phase9Config):
            raise TypeError("config must be a Phase9Config instance.")
        config.validate()

        workspace_root = Path(config.security.workspace_root)
        if not workspace_root.is_absolute():
            raise ConfigurationError(
                "security.workspace_root must be an absolute path."
            )
        workspace_root.mkdir(parents=True, exist_ok=True)

        resolved_state = Path(
            state_root
            if state_root is not None
            else workspace_root.parent / "phase9_state"
        )
        resolved_state.mkdir(parents=True, exist_ok=True)

        audit = AuditLog(
            resolved_state / "audit" / "phase9_audit.jsonl",
            enabled=config.security.audit_enabled,
        )
        approvals = ApprovalGate(audit)

        sandbox_guard = PathGuard(
            workspace_root,
            allow_symlinks=config.security.allow_symlinks,
            allow_reparse_points=config.security.allow_reparse_points,
            allow_absolute_operation_paths=(
                config.security.allow_absolute_operation_paths
            ),
            max_path_components=config.security.max_path_components,
            max_file_bytes=config.security.max_file_bytes,
            reserved_prefixes=(BACKUP_DIRECTORY,),
        )
        backup_guard = PathGuard(
            workspace_root,
            max_path_components=config.security.max_path_components,
            max_file_bytes=max(config.security.max_file_bytes, 8_000_000),
        )

        backups = BackupStore(
            backup_guard,
            directory=BACKUP_DIRECTORY,
            retention=config.security.backup_retention,
            audit=audit,
            clock=clock,
        )
        workspace = WorkspaceManager(
            sandbox_guard,
            config.security,
            approval_gate=approvals,
            audit=audit,
            backups=backups,
            clock=clock,
        )

        from ..resources.monitor import ResourceMonitor
        from ..resources.governor import ResourceGovernor

        if monitor is None:
            monitor = ResourceMonitor(
                sample_seconds=config.resources.telemetry_sample_seconds,
                max_age_seconds=config.resources.telemetry_max_age_seconds,
                clock=clock,
            )
        governor = ResourceGovernor(
            config.resources, monitor=monitor, audit=audit, clock=clock
        )

        from ..providers.registry import ProviderRegistry

        providers = ProviderRegistry.from_config(
            config, transport=transport, audit=audit
        )

        from ..agents.directory import AgentDirectory
        from ..agents.selector import AgentSelector
        from ..collaboration.coordinator import CollaborationCoordinator

        directory = AgentDirectory()
        selector = AgentSelector(
            directory,
            config=config.collaboration,
            governor=governor,
        )
        coordinator = CollaborationCoordinator(
            config.collaboration,
            audit=audit,
            directory=directory,
            clock=clock,
        )

        planner = TaskPlanner(config.planning, providers=providers,
                              audit=audit, clock=clock)
        replanner = Replanner(config.planning, audit=audit, clock=clock)
        handlers = StepHandlerRegistry()

        policy = PolicyEngine(directory, audit=audit)

        runtime = cls(
            config=config,
            audit=audit,
            approval_gate=approvals,
            sandbox_guard=sandbox_guard,
            backup_guard=backup_guard,
            backups=backups,
            workspace=workspace,
            monitor=monitor,
            governor=governor,
            providers=providers,
            directory=directory,
            selector=selector,
            coordinator=coordinator,
            planner=planner,
            replanner=replanner,
            handlers=handlers,
            executor=None,  # type: ignore[arg-type]  # assigned below
            policy=policy,
            state_root=resolved_state,
            clock=clock,
        )

        runtime.executor = PlanExecutor(
            config.planning,
            selector=selector,
            governor=governor,
            handlers=handlers,
            replanner=replanner,
            coordinator=coordinator,
            permission_hook=runtime.check_step_permission,
            permission_requirements=required_permissions,
            audit=audit,
            clock=clock,
        )

        runtime._install_default_handlers()

        audit.record(
            action="runtime.started",
            actor="phase9",
            outcome="ready",
            details={
                "workspace_root": str(workspace_root),
                "state_root": str(resolved_state),
                "deepseek_enabled": config.deepseek_enabled(),
                "enabled_providers": [
                    provider.provider_id for provider in config.enabled_providers()
                ],
            },
        )
        return runtime

    def _install_default_handlers(self) -> None:
        """Install the handlers Phase 9 can guarantee it implements itself.

        Every default handler is pure, deterministic and offline except
        ``research``, which needs a model provider and fails honestly when none
        is available.  No default handler writes to the workspace: writing is
        always an explicit, approved ``WorkspaceManager`` call.
        """
        self.handlers.register("analysis", self._handle_analysis)
        self.handlers.register("design", self._handle_design)
        self.handlers.register("documentation", self._handle_documentation)
        self.handlers.register("verification", self._handle_verification)
        self.handlers.register("research", self._handle_research)

    # -- authorisation ----------------------------------------------------- #
    def check_step_permission(self, step: PlanStep, agent_id: str) -> None:
        """Two-layer check: exact-match grant, then declared permission scope.

        Delegated to :class:`PolicyEngine` so the policy has exactly one
        implementation shared by selection, execution and documentation.
        """
        self.policy.require(
            agent_id, step.capability, step_id=step.step_id
        )

    def grant(self, agent_id: str, capability: str) -> None:
        """Grant one exact capability to one agent, and audit the grant."""
        if self.policy is None:  # pragma: no cover - defensive
            raise WorkspaceError(
                "The permission policy is unavailable; refusing to run "
                "without authorisation enforcement."
            )
        self.policy.grant(agent_id, capability)

    def revoke(self, agent_id: str, capability: str) -> bool:
        if self.policy is None:  # pragma: no cover - defensive
            return False
        return self.policy.revoke(agent_id, capability)

    def register_agent(self, profile: Any, *, grants: Iterable[str] | None = None
                       ) -> Any:
        """Register an agent profile and authorise exactly its capabilities."""
        self.directory.register(profile)
        capabilities = sorted(getattr(profile, "capabilities", {}) or {})
        authorised = sorted(set(grants)) if grants is not None else capabilities
        unknown = [item for item in authorised if item not in capabilities]
        if unknown:
            raise ConfigurationError(
                "Cannot grant capabilities the agent does not declare.",
                agent_id=getattr(profile, "agent_id", "?"),
                unknown=unknown,
            )
        for capability in authorised:
            self.grant(profile.agent_id, capability)
        self.audit.record(
            action="agent.registered",
            actor="phase9",
            outcome="registered",
            details={
                "agent_id": getattr(profile, "agent_id", "?"),
                "capabilities": capabilities,
                "authorised": authorised,
                "permissions": sorted(
                    set(getattr(profile, "permission_scope", frozenset()))
                ),
            },
        )
        return profile

    def register_handler(self, capability: str, handler: StepHandler) -> None:
        self.handlers.register(capability, handler)

    # -- planning / execution ---------------------------------------------- #
    def plan(
        self,
        task: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        required_capabilities: Iterable[str] = (),
        use_model: Optional[bool] = None,
    ) -> ExecutionPlan:
        return self.planner.plan(
            task,
            context=context,
            required_capabilities=required_capabilities,
            use_model=use_model,
        )

    def submit(
        self,
        task: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        requested_by: str = "operator",
    ) -> Submission:
        plan = self.plan(task, context=context)
        submission = Submission(
            submission_id=str(uuid.uuid4()),
            task=task,
            plan=plan,
            requested_by=requested_by,
            created_at=self._clock(),
            context=dict(context or {}),
        )
        self._submissions[submission.submission_id] = submission
        self.audit.record(
            action="task.submitted",
            actor=requested_by,
            outcome="planned",
            details={
                "submission_id": submission.submission_id,
                "plan_id": plan.plan_id,
                "steps": len(plan.steps),
                "capabilities": [step.capability for step in plan.steps],
            },
        )
        return submission

    def execute(
        self,
        submission_id: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> ExecutionResult:
        submission = self._submissions.get(submission_id)
        if submission is None:
            raise KeyError(f"Unknown submission: {submission_id}")

        merged = dict(submission.context)
        merged.update(context or {})
        merged.setdefault("workspace", self.workspace)
        merged.setdefault("approval_gate", self.approvals)
        merged.setdefault("providers", self.providers)

        result = self.executor.run(
            submission.plan, context=merged, on_event=on_event
        )
        submission.result = result
        submission.status = result.status
        return result

    def run(
        self,
        task: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        requested_by: str = "operator",
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> ExecutionResult:
        submission = self.submit(
            task, context=context, requested_by=requested_by
        )
        return self.execute(
            submission.submission_id, context=context, on_event=on_event
        )

    def submission(self, submission_id: str) -> Submission:
        submission = self._submissions.get(submission_id)
        if submission is None:
            raise KeyError(f"Unknown submission: {submission_id}")
        return submission

    def submissions(self) -> list[Submission]:
        return list(self._submissions.values())

    # -- introspection ----------------------------------------------------- #
    def health(self) -> dict[str, Any]:
        """Local, network-free health summary."""
        chain = self.audit.verify_chain()
        deepseek = self.config.provider("deepseek")
        credential_present = bool(
            deepseek is not None and deepseek.has_credential()
        )
        return {
            "phase": "9",
            "status": "STAGING_ONLY",
            "workspace_root": str(self.guard.root),
            "state_root": str(self.state_root),
            "audit_chain_valid": bool(chain.get("valid")),
            "audit_records": chain.get("records", 0),
            "deepseek_enabled": self.config.deepseek_enabled(),
            "deepseek_credential_present": credential_present,
            "enabled_providers": [
                provider.provider_id
                for provider in self.config.enabled_providers()
            ],
            "agents": len(self.directory.all()),
            "handlers": self.handlers.capabilities(),
            "approvals": self.approvals.inventory(),
        }

    def status(self) -> dict[str, Any]:
        resources = {}
        try:
            resources = self.governor.status()
        except Exception as exc:  # pragma: no cover - defensive
            resources = {"error": type(exc).__name__}
        return {
            "health": self.health(),
            "resources": resources,
            "workspace": self.workspace.status(),
            "agents": self.directory.snapshot(),
            "submissions": [item.to_dict() for item in self._submissions.values()],
            "audit": self.audit.summary(),
        }

    def provider_health(self) -> dict[str, Any]:
        """Explicit provider probe. May touch the network for remote providers."""
        return self.providers.health_all()

    # -- default handlers -------------------------------------------------- #
    @staticmethod
    def _task_text(step: PlanStep, context: Mapping[str, Any]) -> str:
        return str(
            step.parameters.get("task") or context.get("task") or step.title
        )

    def _handle_analysis(
        self, step: PlanStep, context: Mapping[str, Any]
    ) -> dict[str, Any]:
        task = self._task_text(step, context)
        words = [item for item in task.replace(",", " ").split() if item]
        keywords = sorted(
            {item.lower().strip(".:;()[]") for item in words if len(item) > 4}
        )
        return {
            "handler": "analysis.local",
            "task": task,
            "word_count": len(words),
            "keywords": keywords[:20],
            "note": (
                "Deterministic local analysis of the task description. "
                "No model was invoked."
            ),
        }

    def _handle_design(
        self, step: PlanStep, context: Mapping[str, Any]
    ) -> dict[str, Any]:
        plan = context.get("plan")
        steps = getattr(plan, "steps", []) or []
        return {
            "handler": "design.local",
            "outline": [item.title for item in steps] or [step.title],
            "note": "Derived from the current plan structure.",
        }

    def _handle_documentation(
        self, step: PlanStep, context: Mapping[str, Any]
    ) -> dict[str, Any]:
        task = self._task_text(step, context)
        markdown = (
            f"# Outcome\n\n"
            f"- Task: {task}\n"
            f"- Phase: 9 (staging)\n"
            f"- Generated by: Phase 9 documentation handler (local)\n\n"
            f"This document was produced deterministically without a model "
            f"call and was not written to disk by this handler.\n"
        )
        return {"handler": "documentation.local", "markdown": markdown}

    def _handle_verification(
        self, step: PlanStep, context: Mapping[str, Any]
    ) -> dict[str, Any]:
        expectations = step.parameters.get("expect_files") or []
        if not isinstance(expectations, (list, tuple)):
            raise WorkspaceError(
                "expect_files must be a list of workspace-relative paths.",
                step_id=step.step_id,
            )

        results: list[dict[str, Any]] = []
        for relative in expectations:
            try:
                info = self.workspace.stat(str(relative))
                results.append({
                    "relative": str(relative),
                    "exists": bool(info.get("exists")),
                    "size": info.get("size"),
                })
            except Phase9Error as exc:
                results.append({
                    "relative": str(relative),
                    "exists": False,
                    "error": exc.describe()["code"],
                })

        if not expectations:
            return {
                "handler": "verification.local",
                "checked": [],
                "note": (
                    "No expectations were supplied; nothing was verified. "
                    "This is not a pass result."
                ),
            }

        missing = [item for item in results if not item.get("exists")]
        return {
            "handler": "verification.local",
            "checked": results,
            "missing": missing,
            "passed": not missing,
        }

    def _handle_research(
        self, step: PlanStep, context: Mapping[str, Any]
    ) -> dict[str, Any]:
        from ..providers.base import ProviderRequest

        task = self._task_text(step, context)
        request = ProviderRequest(
            prompt=f"Summarise what is needed to accomplish: {task}",
            max_tokens=400,
            temperature=0.0,
        )
        try:
            response = self.providers.generate(
                request, capability="text_generation", allow_fallback=True
            )
        except ProviderUnavailableError as exc:
            raise ProviderUnavailableError(
                "No model provider is available for the research step.",
                provider_id=None,
            ) from exc
        return {
            "handler": "research.provider",
            "provider_id": response.provider_id,
            "model": response.model,
            "text": response.text,
        }

    # -- shutdown ---------------------------------------------------------- #
    def shutdown(self) -> None:
        try:
            self.providers.close()
        finally:
            self.audit.record(
                action="runtime.stopped",
                actor="phase9",
                outcome="stopped",
                details={"submissions": len(self._submissions)},
            )


__all__ = [
    "Phase9Runtime",
    "Submission",
    "StepHandlerRegistry",
    "CAPABILITY_PERMISSIONS",
    "PHASE9_SKILL",
    "PHASE9_OPERATION",
    "BACKUP_DIRECTORY",
]
