"""Intelligent task planning.

Two planning sources, in priority order:

1. **Heuristic (always available).** A deterministic intent classifier maps the
   task text onto a canonical capability pipeline.  This is the fallback and the
   baseline the tests assert against.
2. **Model assistance (optional).** When a configured provider is reachable and
   ``PlanningConfig.use_model_assistance`` is enabled, the planner asks the model
   for a JSON plan and then validates it *strictly*:
   unknown capabilities are rejected, dependency ids must point at earlier
   declared steps, cycles are rejected, and the step count must respect
   ``PlanningConfig.max_steps``.  Any deviation falls back to the heuristic plan
   and records why.  The model is never trusted to emit executable code and is
   never given the ability to widen its own permissions.

The planner is pure with respect to the filesystem: it never writes files and
never executes a step.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from ..audit import AuditLog, NullAuditLog
from ..config import PlanningConfig
from ..errors import Phase9Error, ProviderError
from .model import ExecutionPlan, PlanStep

#: Canonical Phase 9 capability vocabulary.  Agent profiles must use these
#: names for the built-in heuristic plans to be selectable.
ALLOWED_CAPABILITIES: tuple[str, ...] = (
    "analysis",
    "research",
    "design",
    "implementation",
    "simulation",
    "optimization",
    "debugging",
    "verification",
    "documentation",
)

#: Tool tokens an agent profile can advertise.
TOOL_WORKSPACE = "workspace"
TOOL_MODEL = "model"


@dataclass(frozen=True)
class Intent:
    capability: str
    confidence: float
    matched: tuple[str, ...]
    required_tools: tuple[str, ...] = ()
    complexity: int = 1


#: (capability, ordered keyword patterns, required tools, base complexity)
INTENT_RULES: tuple[
    tuple[str, tuple[str, ...], tuple[str, ...], int], ...
] = (
    (
        "debugging",
        (r"\bdebug", r"\bfix\b", r"\brepair", r"\berror", r"\bfail",
         r"\bbroken", r"\bregression"),
        (TOOL_WORKSPACE, TOOL_MODEL),
        3,
    ),
    (
        "optimization",
        (r"\boptimi[sz]", r"\btune\b", r"\bperformance", r"\bspeed up",
         r"\breduce\b"),
        (TOOL_WORKSPACE,),
        3,
    ),
    (
        "simulation",
        (r"\bsimulat", r"\brun\b", r"\bexecute\b", r"\bmodel\b",
         r"\bsimulink", r"\bmatlab"),
        (TOOL_WORKSPACE,),
        3,
    ),
    (
        "research",
        (r"\bresearch", r"\binvestigate", r"\bfind\b", r"\bsearch",
         r"\bexplore", r"\bcompare"),
        (TOOL_MODEL, TOOL_WORKSPACE),
        2,
    ),
    (
        "implementation",
        (r"\bimplement", r"\bbuild\b", r"\bcreate\b", r"\bwrite\b",
         r"\badd\b", r"\bgenerate", r"\brefactor"),
        (TOOL_WORKSPACE,),
        3,
    ),
    (
        "design",
        (r"\bdesign", r"\barchitect", r"\bplan\b", r"\bspecif"),
        (TOOL_MODEL,),
        2,
    ),
    (
        "verification",
        (r"\btest", r"\bverify", r"\bvalidate", r"\bcheck\b", r"\bassert"),
        (TOOL_WORKSPACE,),
        2,
    ),
    (
        "documentation",
        (r"\bdocument", r"\breport\b", r"\bexplain", r"\breadme",
         r"\bdescribe"),
        (TOOL_WORKSPACE,),
        1,
    ),
    (
        "analysis",
        (r"\banaly[sz]", r"\binspect", r"\breview", r"\bunderstand",
         r"\bassess", r"\baudit"),
        (TOOL_MODEL,),
        2,
    ),
)

#: Capabilities that must appear in every plan, in this order.
MANDATORY_HEAD = ("analysis",)
MANDATORY_TAIL = ("verification", "documentation")

#: Canonical execution order for the whole capability vocabulary.
PIPELINE_ORDER: tuple[str, ...] = (
    "research",
    "analysis",
    "design",
    "implementation",
    "debugging",
    "simulation",
    "optimization",
    "verification",
    "documentation",
)


class TaskPlanner:
    """Produce a validated :class:`ExecutionPlan` for a task description."""

    def __init__(
        self,
        config: PlanningConfig,
        *,
        providers: Any | None = None,
        audit: Optional[AuditLog] = None,
        clock=time.time,
    ) -> None:
        self.config = config
        self.providers = providers
        self.audit = audit or NullAuditLog()
        self._clock = clock

    # -- public API -------------------------------------------------------- #
    def plan(
        self,
        task: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        required_capabilities: Iterable[str] = (),
        use_model: Optional[bool] = None,
    ) -> ExecutionPlan:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-empty string.")

        requested = self._normalise_capabilities(required_capabilities)
        data = dict(context or {})

        wants_model = (
            self.config.use_model_assistance if use_model is None else bool(
                use_model
            )
        )

        heuristic = self._heuristic_plan(task, requested, data)

        if not wants_model:
            self._audit("plan.created", heuristic, source="heuristic",
                        reason="model assistance disabled")
            return heuristic

        model_plan = self._model_plan(task, requested, data)
        if model_plan is not None:
            self._audit("plan.created", model_plan, source="model",
                        reason="model-assisted plan accepted")
            return model_plan

        heuristic.metadata["model_fallback"] = True
        heuristic.rationale = (
            heuristic.rationale
            + " Model assistance was requested but produced no valid plan; "
            "the deterministic heuristic plan was used instead."
        )
        self._audit("plan.created", heuristic, source="heuristic",
                    reason="model assistance failed validation")
        return heuristic

    def explain(self, plan: ExecutionPlan) -> str:
        """Render a human-readable explanation of the plan."""
        lines = [
            f"Plan {plan.plan_id} (revision {plan.revision}, "
            f"source={plan.source})",
            f"Task: {plan.task}",
            f"Goal: {plan.goal}",
        ]
        if plan.rationale:
            lines.append(f"Rationale: {plan.rationale}")
        lines.append(f"Steps ({len(plan.steps)}):")
        order = plan.topological_order()
        for index, step in enumerate(order, start=1):
            dependencies = ", ".join(step.depends_on) or "none"
            tools = ", ".join(step.required_tools) or "none"
            lines.append(
                f"  {index}. [{step.step_id}] {step.title} "
                f"(capability={step.capability}, priority={step.priority}, "
                f"complexity={step.complexity}, tools={tools}, "
                f"depends_on={dependencies})"
            )
            if step.description:
                lines.append(f"     {step.description}")
            if step.rationale:
                lines.append(f"     why: {step.rationale}")
        return "\n".join(lines)

    # -- heuristic planning ------------------------------------------------ #
    def _heuristic_plan(
        self,
        task: str,
        requested: tuple[str, ...],
        context: Mapping[str, Any],
    ) -> ExecutionPlan:
        intents = self._classify(task)
        capabilities = self._assemble_capabilities(intents, requested)
        capabilities = self._clamp_capabilities(capabilities)

        steps: list[PlanStep] = []
        previous_ids: list[str] = []
        for index, capability in enumerate(capabilities, start=1):
            step_id = f"step-{index:02d}"
            intent = next(
                (item for item in intents if item.capability == capability),
                None,
            )
            tools = self._default_tools(capability)
            complexity = intent.complexity if intent else self._base_complexity(
                capability
            )
            priority = self._priority_for(capability, index)
            depends = ()
            if capability in MANDATORY_TAIL:
                depends = tuple(previous_ids)
            elif previous_ids and capability != MANDATORY_HEAD[0]:
                depends = (previous_ids[-1],)

            steps.append(
                PlanStep(
                    step_id=step_id,
                    title=self._title_for(capability, task),
                    capability=capability,
                    description=self._description_for(capability, task),
                    required_tools=tools,
                    depends_on=depends,
                    priority=priority,
                    parameters={"task": task, **dict(context)},
                    timeout_seconds=self.config.step_timeout_seconds,
                    max_attempts=2,
                    complexity=max(1, min(complexity, 5)),
                    rationale=(
                        f"Selected because the task matched intent "
                        f"'{capability}' with confidence "
                        f"{(intent.confidence if intent else 0.5):.2f}."
                    ),
                )
            )
            previous_ids.append(step_id)

        goal = self._goal_from(task, capabilities)
        rationale = (
            "Deterministic keyword decomposition. Detected intents: "
            + (", ".join(sorted({item.capability for item in intents})) or "none")
            + f". Capability pipeline: {' -> '.join(capabilities)}."
        )

        plan = ExecutionPlan.create(
            task=task,
            goal=goal,
            steps=steps,
            source="heuristic",
            rationale=rationale,
            clock=self._clock,
        )
        plan.validate(max_steps=self.config.max_steps)
        return plan

    # -- model-assisted planning ------------------------------------------- #
    def _model_plan(
        self,
        task: str,
        requested: tuple[str, ...],
        context: Mapping[str, Any],
    ) -> Optional[ExecutionPlan]:
        provider_response = self._ask_model(task, requested, context)
        if provider_response is None:
            return None

        payload = self._extract_json(provider_response)
        if payload is None:
            self._audit_plan_failure("model output was not valid JSON")
            return None

        try:
            steps = self._steps_from_model_payload(payload)
            plan = ExecutionPlan.create(
                task=task,
                goal=str(payload.get("goal") or self._default_goal(task)),
                steps=steps,
                source="model",
                rationale=str(
                    payload.get("rationale")
                    or "Plan proposed by the configured model provider and "
                       "accepted after strict validation."
                ),
                clock=self._clock,
            )
            plan.validate(max_steps=self.config.max_steps)
        except Phase9Error as exc:
            self._audit_plan_failure(
                f"model plan rejected by validation: {exc}"
            )
            return None
        except Exception as exc:  # pragma: no cover - defensive
            self._audit_plan_failure(
                "model plan rejected: %s" % type(exc).__name__
            )
            return None

        plan.metadata["provider_response_model"] = self._response_model(
            provider_response
        )
        return plan

    def _ask_model(
        self,
        task: str,
        requested: tuple[str, ...],
        context: Mapping[str, Any],
    ) -> Any | None:
        if self.providers is None:
            return None

        try:
            from ..providers.base import ProviderRequest
        except Exception:  # pragma: no cover - provider package absent
            return None

        prompt = self._build_planning_prompt(task, requested, context)
        request = ProviderRequest(
            prompt=prompt,
            system=(
                "You are a planning component inside a local AI operating "
                "system. Reply with a single JSON object only. Never include "
                "code, never request new permissions and never reference "
                "capabilities outside the allowed list."
            ),
            max_tokens=900,
            temperature=0.0,
        )

        try:
            return self.providers.generate(
                request, capability="planning", allow_fallback=False
            )
        except ProviderError as exc:
            self._audit_plan_failure(
                f"provider error during planning: {exc.describe()['code']}"
            )
            return None
        except Exception as exc:  # pragma: no cover - defensive
            self._audit_plan_failure(
                "unexpected provider failure: %s" % type(exc).__name__
            )
            return None

    def _build_planning_prompt(
        self,
        task: str,
        requested: tuple[str, ...],
        context: Mapping[str, Any],
    ) -> str:
        capabilities = ", ".join(ALLOWED_CAPABILITIES)
        requested_text = ", ".join(requested) if requested else "none"
        context_text = json.dumps(
            {key: str(value)[:200] for key, value in context.items()},
            sort_keys=True,
        )
        return (
            "Produce an execution plan for the following task.\n\n"
            f"TASK: {task}\n"
            f"REQUESTED CAPABILITIES: {requested_text}\n"
            f"CONTEXT: {context_text}\n"
            f"MAX STEPS: {self.config.max_steps}\n\n"
            f"Allowed capabilities: {capabilities}.\n"
            "Return JSON with this exact shape:\n"
            '{"goal": "one sentence", "rationale": "why this decomposition", '
            '"steps": [{"step_id": "step-01", "title": "...", '
            '"capability": "<allowed capability>", "description": "...", '
            '"depends_on": ["step-id", ...], "required_tools": ["workspace"], '
            '"complexity": 1}]}\n'
            "Rules: step ids must be unique and dependency ids must refer to "
            "earlier steps. Use only the allowed capabilities. "
            "Do not include any other text."
        )

    def _steps_from_model_payload(self, payload: Mapping[str, Any]
                                  ) -> list[PlanStep]:
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            from ..errors import PlanValidationError

            raise PlanValidationError("Model plan has no steps.")
        if len(raw_steps) > self.config.max_steps:
            from ..errors import PlanLimitError

            raise PlanLimitError(
                "Model plan exceeds the step limit.",
                steps=len(raw_steps),
                limit=self.config.max_steps,
            )

        steps: list[PlanStep] = []
        declared: set[str] = set()
        for index, raw in enumerate(raw_steps, start=1):
            if not isinstance(raw, Mapping):
                from ..errors import PlanValidationError

                raise PlanValidationError("Model step must be an object.")

            step_id = str(raw.get("step_id") or f"step-{index:02d}").strip()
            if step_id in declared:
                from ..errors import PlanValidationError

                raise PlanValidationError(
                    "Model plan reuses a step id.", step_id=step_id
                )

            capability = str(raw.get("capability", "")).strip()
            if capability not in ALLOWED_CAPABILITIES:
                from ..errors import PlanValidationError

                raise PlanValidationError(
                    "Model plan requested an unknown capability.",
                    capability=capability,
                    allowed=list(ALLOWED_CAPABILITIES),
                )

            depends = raw.get("depends_on") or ()
            if not isinstance(depends, (list, tuple)):
                from ..errors import PlanValidationError

                raise PlanValidationError(
                    "depends_on must be a list.", step_id=step_id
                )
            for dependency in depends:
                if str(dependency) not in declared:
                    from ..errors import PlanValidationError

                    raise PlanValidationError(
                        "Model plan depends on an undeclared or later step.",
                        step_id=step_id,
                        dependency=str(dependency),
                    )

            raw_tools = raw.get("required_tools") or ()
            if not isinstance(raw_tools, (list, tuple)):
                from ..errors import PlanValidationError

                raise PlanValidationError(
                    "required_tools must be a list.", step_id=step_id
                )
            tools = tuple(
                str(tool) for tool in raw_tools
                if str(tool) in {TOOL_WORKSPACE, TOOL_MODEL}
            )

            complexity = raw.get("complexity", 1)
            try:
                complexity_value = int(complexity)
            except (TypeError, ValueError):
                complexity_value = 1
            complexity_value = max(1, min(complexity_value, 5))

            priority = raw.get("priority", 0)
            try:
                priority_value = int(priority)
            except (TypeError, ValueError):
                priority_value = 0

            step = PlanStep(
                step_id=step_id,
                title=str(raw.get("title") or f"Step {index}"),
                capability=capability,
                description=str(raw.get("description") or ""),
                required_tools=tools,
                depends_on=tuple(str(item) for item in depends),
                priority=priority_value,
                parameters={"task": payload.get("goal", "")},
                timeout_seconds=self.config.step_timeout_seconds,
                max_attempts=2,
                complexity=complexity_value,
                rationale="Proposed by the configured model provider.",
            )
            step.validate()
            steps.append(step)
            declared.add(step_id)

        return steps

    # -- intent classification --------------------------------------------- #
    @staticmethod
    def _assemble_capabilities(
        intents: list["Intent"], requested: tuple[str, ...]
    ) -> list[str]:
        """Build the ordered capability pipeline for a heuristic plan.

        The head (``analysis``) and the tail (``verification``,
        ``documentation``) are always present.  Everything detected in between
        is sorted into the canonical execution order, so a task that mentions
        "document" still documents *after* it implements.
        """
        middle: list[str] = []

        def add(capability: str) -> None:
            if capability in MANDATORY_HEAD or capability in MANDATORY_TAIL:
                return
            if capability not in middle:
                middle.append(capability)

        for intent in intents:
            add(intent.capability)
        for capability in requested:
            add(capability)

        # Do not invent an ``implementation`` step for a simulation /
        # optimisation / debugging request.  The planner's job is to decompose
        # what was asked for; adding a capability the caller never requested
        # makes such a plan impossible to execute whenever no implementation
        # handler is registered, and aborts execution before the requested
        # capability is ever attempted.  Callers that do want implementation
        # say so ("implement", "build", "write") or pass
        # ``required_capabilities=["implementation"]``.
        middle.sort(key=lambda item: PIPELINE_ORDER.index(item))

        selected = set(MANDATORY_HEAD) | set(middle) | set(MANDATORY_TAIL)
        capabilities = [item for item in PIPELINE_ORDER if item in selected]

        # Anything the vocabulary does not know about keeps its position last
        # but before the mandatory tail.
        return capabilities

    def _clamp_capabilities(self, capabilities: list[str]) -> list[str]:
        """Trim the optional middle when ``max_steps`` cannot fit the plan."""
        limit = self.config.max_steps
        if len(capabilities) <= limit:
            return capabilities

        head = [item for item in capabilities if item in MANDATORY_HEAD]
        tail = [item for item in capabilities if item in MANDATORY_TAIL]
        middle = [
            item for item in capabilities
            if item not in MANDATORY_HEAD and item not in MANDATORY_TAIL
        ]
        budget = max(0, limit - len(head) - len(tail))
        clamped = head + middle[:budget] + tail
        self.audit.record(
            action="plan.capabilities_clamped",
            actor="planner",
            outcome="trimmed",
            details={
                "requested": capabilities,
                "kept": clamped[:limit],
                "limit": limit,
            },
        )
        return clamped[:limit]

    def _classify(self, task: str) -> list[Intent]:
        lowered = task.lower()
        intents: list[Intent] = []
        for capability, patterns, tools, complexity in INTENT_RULES:
            matches = tuple(
                pattern for pattern in patterns if re.search(pattern, lowered)
            )
            if not matches:
                continue
            confidence = min(0.35 + 0.15 * len(matches), 0.95)
            intents.append(
                Intent(
                    capability=capability,
                    confidence=confidence,
                    matched=matches,
                    required_tools=tools,
                    complexity=complexity,
                )
            )
        intents.sort(key=lambda item: (-item.confidence, item.capability))
        return intents

    # -- small helpers ----------------------------------------------------- #
    @staticmethod
    def _normalise_capabilities(values: Iterable[str]) -> tuple[str, ...]:
        if values is None:
            return ()
        result: list[str] = []
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    "required_capabilities entries must be non-empty strings."
                )
            capability = value.strip()
            if capability not in ALLOWED_CAPABILITIES:
                from ..errors import PlanValidationError

                raise PlanValidationError(
                    "Unknown capability requested.",
                    capability=capability,
                    allowed=list(ALLOWED_CAPABILITIES),
                )
            if capability not in result:
                result.append(capability)
        return tuple(result)

    @staticmethod
    def _default_tools(capability: str) -> tuple[str, ...]:
        return {
            "analysis": (),
            "research": (TOOL_MODEL,),
            "design": (),
            "implementation": (TOOL_WORKSPACE,),
            "simulation": (TOOL_WORKSPACE,),
            "optimization": (TOOL_WORKSPACE,),
            "debugging": (TOOL_WORKSPACE,),
            "verification": (TOOL_WORKSPACE,),
            "documentation": (TOOL_WORKSPACE,),
        }.get(capability, ())

    @staticmethod
    def _base_complexity(capability: str) -> int:
        return {
            "analysis": 2,
            "design": 2,
            "implementation": 3,
            "simulation": 3,
            "optimization": 3,
            "debugging": 3,
            "verification": 2,
            "documentation": 1,
            "research": 2,
        }.get(capability, 2)

    @staticmethod
    def _priority_for(capability: str, index: int) -> int:
        base = {
            "analysis": 90,
            "research": 80,
            "design": 70,
            "implementation": 60,
            "debugging": 65,
            "simulation": 55,
            "optimization": 50,
            "verification": 40,
            "documentation": 10,
        }.get(capability, 30)
        return base - index

    @staticmethod
    def _title_for(capability: str, task: str) -> str:
        return {
            "analysis": "Analyse the request",
            "research": "Gather supporting information",
            "design": "Design the approach",
            "implementation": "Implement the requested change",
            "simulation": "Run the model or simulation",
            "optimization": "Optimise the result",
            "debugging": "Diagnose and repair the failure",
            "verification": "Verify the outcome",
            "documentation": "Document the outcome",
        }.get(capability, f"Execute {capability}")

    @staticmethod
    def _description_for(capability: str, task: str) -> str:
        return (
            f"Perform the '{capability}' phase for the task: "
            f"{task[:200]}"
        )

    @staticmethod
    def _goal_from(task: str, capabilities: Iterable[str]) -> str:
        return (
            f"Complete '{task[:160]}' through the phases: "
            + " -> ".join(capabilities)
        )

    @staticmethod
    def _default_goal(task: str) -> str:
        return f"Complete the task: {task[:160]}"

    @staticmethod
    def _extract_json(text: Any) -> Optional[Mapping[str, Any]]:
        if isinstance(text, Mapping):
            return text
        raw = getattr(text, "text", text)
        if not isinstance(raw, str):
            return None
        raw = raw.strip()
        if not raw:
            return None
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw.strip())
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                parsed = json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return None
        return parsed if isinstance(parsed, Mapping) else None

    @staticmethod
    def _response_model(response: Any) -> str:
        return str(getattr(response, "model", "") or "")

    def _audit_plan_failure(self, reason: str) -> None:
        self.audit.record(
            action="plan.model_rejected",
            actor="planner",
            outcome="fallback",
            details={"reason": reason},
        )

    def _audit(
        self,
        action: str,
        plan: ExecutionPlan,
        *,
        source: str,
        reason: str,
    ) -> None:
        self.audit.record(
            action=action,
            actor="planner",
            outcome="created",
            details={
                "plan_id": plan.plan_id,
                "source": source,
                "steps": len(plan.steps),
                "capabilities": [step.capability for step in plan.steps],
                "goal": plan.goal,
                "reason": reason,
            },
        )


__all__ = [
    "TaskPlanner",
    "ALLOWED_CAPABILITIES",
    "INTENT_RULES",
    "TOOL_MODEL",
    "TOOL_WORKSPACE",
]
