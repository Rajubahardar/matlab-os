"""Centralized MATLAB / Simulink MCP operation policy.

The MCP server exposes fourteen tools.  Availability is not authorization:
this module classifies the *operation* a tool performs and decides whether the
Local AI OS control plane may run it.  The default posture is read-only.

Nothing here inspects the machine, spawns MATLAB, or trusts the caller.  A tool
that is not explicitly classified is treated as ``UNKNOWN`` and refused.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Mapping


class OperationClass(str, Enum):
    """How a MATLAB/Simulink tool affects the user's environment."""

    READ = "read"
    MUTATING = "mutating"
    CODE_EXECUTION = "code_execution"
    UNKNOWN = "unknown"


# Tools that only observe state.  Each was verified against the installed
# server's tool descriptions (`model_overview` reports hierarchy, `model_read`
# returns topology, `model_scan` greps the .slx on disk, `check_matlab_code`
# runs the Code Analyzer).
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "detect_matlab_toolboxes",
        "model_overview",
        "model_read",
        "model_scan",
        "model_read_diagnostics",
        "check_matlab_code",
    }
)

# Tools that change a model, save artifacts, or run user scripts on disk.
MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "model_edit",
        "model_test",
        "run_matlab_file",
        "run_matlab_test_file",
    }
)

# Tools that evaluate arbitrary code.  Even a harmless-looking expression can
# call `delete`, `save`, or `system`, so they are never read-only.
CODE_EXECUTION_TOOLS: frozenset[str] = frozenset({"evaluate_matlab_code"})

#: Expressions provably free of side effects.  Anything not matching one of
#: these is treated as mutating-capable, because a single call can delete files,
#: save models, or shell out.  The patterns are anchored and deliberately narrow.
_READ_ONLY_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:version|ver|pwd|whos)\s*;?$", re.IGNORECASE),
    re.compile(
        r"^disp\s*\(\s*(?:version|[\d\s.+\-*/()]+)\s*\)\s*;?$",
        re.IGNORECASE,
    ),
)

MAX_READ_ONLY_CODE_LENGTH = 200


def classify_matlab_code(code: Any) -> OperationClass:
    """Classify the MATLAB code passed to ``evaluate_matlab_code``.

    Returns ``READ`` only for an exact match of a side-effect-free expression.
    Everything else -- including empty or non-string input, over-long code, and
    anything containing a statement separator that could hide a second command
    -- is ``CODE_EXECUTION`` and therefore requires both an explicit
    ``allow_code_execution`` flag and a granted approval.
    """

    if not isinstance(code, str):
        return OperationClass.CODE_EXECUTION

    text = code.strip()
    if not text or len(text) > MAX_READ_ONLY_CODE_LENGTH:
        return OperationClass.CODE_EXECUTION

    for pattern in _READ_ONLY_CODE_PATTERNS:
        if pattern.fullmatch(text):
            return OperationClass.READ

    return OperationClass.CODE_EXECUTION


# Tools whose classification depends on their arguments.
_ARGUMENT_DEPENDENT_TOOLS: frozenset[str] = frozenset(
    {"model_query_params", "model_resolve_params", "model_check"}
)


class MatlabToolPolicy:
    """Decide whether a MATLAB/Simulink MCP tool may run.

    Parameters
    ----------
    allow_mutating:
        Permit tools that change a model or write files.  Off by default.
    allow_code_execution:
        Permit arbitrary MATLAB evaluation.  Off by default.
    allowed_tools / denied_tools:
        Explicit operator overrides.  ``denied_tools`` always wins, so a tool
        can never be re-enabled by an allowlist entry.
    """

    def __init__(
        self,
        *,
        allow_mutating: bool = False,
        allow_code_execution: bool = False,
        allowed_tools: frozenset[str] | set[str] | None = None,
        denied_tools: frozenset[str] | set[str] | None = None,
    ) -> None:
        self.allow_mutating = bool(allow_mutating)
        self.allow_code_execution = bool(allow_code_execution)
        self.allowed_tools = frozenset(allowed_tools or ())
        self.denied_tools = frozenset(denied_tools or ())

        overlap = self.allowed_tools & self.denied_tools
        if overlap:
            raise ValueError(
                "Tools cannot be both allowed and denied: "
                + ", ".join(sorted(overlap))
            )

    # -- classification ---------------------------------------------------- #
    def classify(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> OperationClass:
        """Return the operation class for ``tool_name``."""

        name = str(tool_name or "").strip()
        if not name:
            return OperationClass.UNKNOWN

        if name in READ_ONLY_TOOLS:
            return OperationClass.READ
        if name in MUTATING_TOOLS:
            return OperationClass.MUTATING
        if name in CODE_EXECUTION_TOOLS:
            # Always code execution: the tool itself can run anything, so it is
            # never read-only and is blocked unless explicitly enabled.  Narrow
            # classification only decides whether *approval* is also required.
            return OperationClass.CODE_EXECUTION
        if name in _ARGUMENT_DEPENDENT_TOOLS:
            return self._classify_by_arguments(name, arguments or {})
        return OperationClass.UNKNOWN

    @staticmethod
    def _classify_by_arguments(
        name: str,
        arguments: Mapping[str, Any],
    ) -> OperationClass:
        # Compiling a model mutates its compiled state and can emit artifacts.
        if name == "model_query_params":
            compile_flag = arguments.get("compile")
            if compile_flag in (True, "true", "True", 1):
                return OperationClass.MUTATING
            return OperationClass.READ

        if name == "model_check":
            return OperationClass.READ

        # model_resolve_params only reads model workspace variables.
        return OperationClass.READ

    # -- decision ---------------------------------------------------------- #
    def decide(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return an auditable decision without raising.

        ``requires_approval`` is part of the decision so callers cannot infer a
        weaker rule: every mutation needs approval, and code execution needs it
        unless the expression is on the side-effect-free allowlist.
        """

        decision = self._decide(tool_name, arguments)
        operation = decision.get("operation")

        requires_approval = operation == OperationClass.MUTATING.value
        if operation == OperationClass.CODE_EXECUTION.value:
            requires_approval = (
                classify_matlab_code((arguments or {}).get("code"))
                is not OperationClass.READ
            )
        decision["requires_approval"] = requires_approval
        return decision

    def _decide(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return an auditable decision without raising."""

        name = str(tool_name or "").strip()
        operation = self.classify(name, arguments)

        if not name:
            return self._deny(name, operation, "A tool name is required.")

        if name in self.denied_tools:
            return self._deny(name, operation, "Tool is explicitly denied.")

        if name in self.allowed_tools:
            return self._allow(name, operation, "Tool is explicitly allowed.")

        if operation is OperationClass.READ:
            return self._allow(name, operation, "Read-only operation.")

        if operation is OperationClass.MUTATING:
            if self.allow_mutating:
                return self._allow(name, operation, "Mutating tools are enabled.")
            return self._deny(
                name,
                operation,
                "Read-only policy: this tool modifies models or files.",
            )

        if operation is OperationClass.CODE_EXECUTION:
            if self.allow_code_execution:
                return self._allow(
                    name, operation, "Code execution is explicitly enabled."
                )
            return self._deny(
                name,
                operation,
                "Read-only policy: arbitrary MATLAB evaluation is blocked.",
            )

        return self._deny(
            name,
            operation,
            "Unrecognised tool; refusing by default (deny-by-default).",
        )

    def authorize(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the decision or raise ``PermissionError`` if denied."""

        decision = self.decide(tool_name, arguments)
        if not decision["allowed"]:
            raise PermissionError(decision["reason"])
        return decision

    def is_allowed(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> bool:
        return bool(self.decide(tool_name, arguments)["allowed"])

    # -- helpers ----------------------------------------------------------- #
    @staticmethod
    def _allow(
        name: str, operation: OperationClass, reason: str
    ) -> dict[str, Any]:
        return {
            "allowed": True,
            "tool": name,
            "operation": operation.value,
            "reason": reason,
        }

    @staticmethod
    def _deny(
        name: str, operation: OperationClass, reason: str
    ) -> dict[str, Any]:
        return {
            "allowed": False,
            "tool": name,
            "operation": operation.value,
            "reason": reason,
        }
