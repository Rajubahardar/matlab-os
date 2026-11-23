"""Shared MATLAB capability interface for MATLABAgent.

All skill execution is delegated to the existing skill_handlers
dispatcher. No direct or unapproved MATLAB execution is introduced.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


FUNCTION_INTELLIGENCE = "MATLAB Function Intelligence"
SIMULINK_SKILL = "Simulink Model Development"


@dataclass
class WorkflowStepResult:
    index: int
    skill: str
    operation: str
    status: str
    result: Any = None
    error: Optional[str] = None


@dataclass
class WorkflowResult:
    status: str
    steps: List[WorkflowStepResult] = field(default_factory=list)

    @property
    def passed(self):
        return sum(x.status == "PASS" for x in self.steps)

    @property
    def failed(self):
        return sum(x.status == "FAIL" for x in self.steps)

    def to_dict(self):
        return {
            "status": self.status,
            "passed": self.passed,
            "failed": self.failed,
            "steps": [
                {
                    "index": x.index,
                    "skill": x.skill,
                    "operation": x.operation,
                    "status": x.status,
                    "result": repr(x.result) if x.result is not None else None,
                    "error": x.error,
                }
                for x in self.steps
            ],
        }


class MATLABCapabilityInterface:
    """Reusable capability interface for all MATLAB engineering skills."""

    def __init__(self, agent):
        if agent is None:
            raise ValueError("A MATLABAgent instance is required.")
        self.agent = agent

        # Lazy import avoids a circular import during package initialization.
        from . import skill_handlers
        self.dispatcher = skill_handlers

    def available_skills(self):
        return self.dispatcher.get_skill_profiles()

    def available_operations(self):
        return self.dispatcher.get_available_operations()

    def discover_functions(self, function_names):
        return self.function_intelligence(
            "discover_functions",
            function_names=function_names,
        )

    def inspect_function(self, function_name):
        return self.function_intelligence(
            "inspect_function",
            function_name=function_name,
        )

    def search_documentation(self, query):
        return self.function_intelligence(
            "search_documentation",
            query=query,
        )

    def check_dependencies(self, products=None, license_features=None):
        return self.function_intelligence(
            "check_dependencies",
            products=products or [],
            license_features=license_features or [],
        )

    def find_simulink_blocks(self, block_query, library="simulink"):
        return self.function_intelligence(
            "find_simulink_blocks",
            block_query=block_query,
            library=library,
        )

    def recommend_function(self, query):
        return self.function_intelligence(
            "recommend_function",
            query=query,
        )

    def function_intelligence(self, operation, **params):
        return self.execute_skill_operation(
            FUNCTION_INTELLIGENCE,
            operation,
            params,
            reason="Shared MATLAB capability: " + operation,
        )

    def inspect_output(self, variable):
        return self.function_intelligence(
            "inspect_output",
            variable=variable,
        )

    def verify_output(
        self,
        variable,
        expected_class=None,
        expected_size=None,
        require_finite=True,
    ):
        params = {
            "variable": variable,
            "require_finite": require_finite,
        }
        if expected_class is not None:
            params["expected_class"] = expected_class
        if expected_size is not None:
            params["expected_size"] = expected_size

        return self.function_intelligence("verify_output", **params)

    def execute_skill_operation(
        self,
        skill,
        operation,
        params=None,
        reason=None,
    ):
        """Execute one registered operation through the original dispatcher."""

        if not getattr(self.agent, "initialized", False):
            raise RuntimeError("Initialize MATLABAgent before using capabilities.")

        operations = self.available_operations()

        if skill not in operations:
            raise ValueError("Unknown or unregistered skill: " + str(skill))

        if operation not in operations[skill]:
            raise ValueError(
                "Unsupported operation '%s' for skill '%s'. Available: %s"
                % (operation, skill, operations[skill])
            )

        return self.dispatcher.execute_skill_handler(
            self.agent,
            skill,
            operation=operation,
            reason=reason or ("Shared capability execution: " + skill + "/" + operation),
            **(params or {}),
        )

    @staticmethod
    def validate_workflow(steps):
        if not isinstance(steps, (list, tuple)) or not steps:
            raise ValueError("Workflow must contain at least one step.")

        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise TypeError("Workflow step %d must be a dictionary." % index)

            for required in ("skill", "operation"):
                if not isinstance(step.get(required), str) or not step[required].strip():
                    raise ValueError(
                        "Workflow step %d requires '%s'." % (index, required)
                    )

            params = step.get("params", {})
            if not isinstance(params, dict):
                raise TypeError("Step params must be a dictionary.")

        return True

    def run_workflow(self, steps, stop_on_error=True):
        """Run registered skill operations sequentially.

        Every operation independently passes through the existing approval
        and execution path. No raw MATLAB code is accepted here.
        """

        self.validate_workflow(steps)
        results = []

        for index, step in enumerate(steps, start=1):
            skill = step["skill"]
            operation = step["operation"]
            params = step.get("params", {})
            reason = step.get(
                "reason",
                "Engineering workflow step %d: %s/%s"
                % (index, skill, operation),
            )

            try:
                value = self.execute_skill_operation(
                    skill,
                    operation,
                    params,
                    reason=reason,
                )
                results.append(
                    WorkflowStepResult(
                        index, skill, operation, "PASS", result=value
                    )
                )
            except Exception as exc:
                results.append(
                    WorkflowStepResult(
                        index, skill, operation, "FAIL",
                        error=type(exc).__name__ + ": " + str(exc),
                    )
                )
                if stop_on_error:
                    break

        status = "PASS" if all(x.status == "PASS" for x in results) else "FAIL"
        return WorkflowResult(status=status, steps=results)

    def run_simulink_workflow(self, steps, stop_on_error=True):
        """Execute an ordered Simulink plan using existing Skill 2 operations."""

        self.validate_workflow(steps)

        for index, step in enumerate(steps, start=1):
            if step["skill"] != SIMULINK_SKILL:
                raise ValueError(
                    "Simulink workflow step %d must use '%s'."
                    % (index, SIMULINK_SKILL)
                )

        return self.run_workflow(steps, stop_on_error=stop_on_error)


def get_matlab_capabilities(agent):
    """Factory shared by the MATLABAgent and its engineering skills."""
    return MATLABCapabilityInterface(agent)
