"""Capability-based agent selection for the Phase 12 ecosystem.

Uses the reused :class:`matlab_os.phase9.agents.selector.AgentSelector` as the
scoring engine and exposes a clean assign answer or a full rejection report.
"""
from __future__ import annotations

from typing import Any, Iterable

from matlab_os.phase9.agents.capability import AgentProfile
from matlab_os.phase9.agents.selector import AgentSelector, SelectionCandidate
from matlab_os.phase9.errors import AgentSelectionError
from matlab_os.integration.agent_ecosystem import contract as ec


class SelectionError(ValueError):
    """No agent satisfies the requirements."""


def _selected(result: Any):
    return getattr(result, 'selected', None) or getattr(result, 'profile', None)


def _candidate_ids(result: Any) -> list[str]:
    return [c.agent_id for c in getattr(result, 'candidates', ())]


class SelectionAdapter:
    """Capability matcher that delegates to the existing selector."""

    def __init__(self, selector: AgentSelector) -> None:
        self.selector = selector

    def select(self, agent_ids: Iterable[str] | None, required_capabilities: Iterable[str],
               *,
               required_tools: Iterable[str] | None = None,
               complexity: int = 1,
               required_permissions: Iterable[str] | None = None) -> dict[str, Any]:
        candidate_ids = list(agent_ids) if agent_ids is not None else None
        try:
            result = self.selector.select(
                required_capabilities=required_capabilities,
                required_tools=required_tools if required_tools is not None else (),
                complexity=complexity,
                required_permissions=required_permissions if required_permissions is not None else (),
                exclude_agents=(),
            )
        except AgentSelectionError as exc:
            raise SelectionError(str(exc)) from exc
        selected = _selected(result)
        if candidate_ids is not None and (
            selected is None or selected.agent_id not in candidate_ids
        ):
            raise SelectionError(
                "no eligible agent within the requested candidates: "
                + ", ".join(sorted(candidate_ids))
            )
        return self._to_assignment(result)

    def assess(self, agent_id: str, required_capabilities: Iterable[str],
               *,
               required_tools: Iterable[str] | None = None,
               complexity: int = 1,
               required_permissions: Iterable[str] | None = None) -> dict[str, Any]:
        result = self.selector.select(
            required_capabilities=required_capabilities,
            required_tools=required_tools,
            complexity=complexity,
            required_permissions=required_permissions,
            exclude_agents=(agent_id,),
        )
        selected = _selected(result)
        return {
            "agent_id": agent_id,
            "eligible": selected is not None and selected.agent_id == agent_id,
            "missing_capabilities": selected.missing_capabilities(required_capabilities) if selected is not None else [],
            "rejected_candidate": _candidate_ids(result),
            "explanation": getattr(result, "explanation", ""),
        }

    def _to_assignment(self, result: Any) -> dict[str, Any]:
        selected = _selected(result)
        profile = ec.AgentContract.from_dict(selected.to_dict())
        score = getattr(result, "score_breakdown", {}).get("total", 0.0) or getattr(result, "score", getattr(result, "total", 0.0) or 0.0)
        return {
            "assigned_agent": profile.agent_id,
            "score": score,
            "explanation": getattr(result, "explanation", ""),
            "candidate_ids": _candidate_ids(result),
            "eligible": profile,
        }
