"""Deterministic, capability-based agent selection for Phase 9 (staging only).

Selection is a two-stage pipeline:

1. **Hard filters** remove agents that cannot legally perform the work.  Every
   rejection records a machine-readable reason such as ``"status:disabled"`` or
   ``"capacity:1/1"``; nothing is ever silently dropped.
2. **Scoring** ranks the survivors.  The score combines capability
   specificity, complexity fit, free capacity, an optional locality preference
   and the inverse cost weight.  Equal scores are broken deterministically by
   ascending ``agent_id``.

If no candidate survives, :class:`~matlab_os.phase9.errors.AgentSelectionError`
is raised with a summary of every rejection reason.  There is intentionally no
fallback selection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from collections.abc import Iterable, Mapping

from ..errors import AgentNotFoundError, AgentSelectionError
from .capability import AgentProfile, AgentStatus
from .directory import AgentDirectory

#: Scoring weights.  Documented so the ranking is reproducible.
SPECIFICITY_WEIGHT = 2.0
COMPLEXITY_WEIGHT = 1.0
CAPACITY_WEIGHT = 1.0
LOCALITY_WEIGHT = 1.0
COST_WEIGHT = 1.0

#: Components present in every score breakdown.
SCORE_COMPONENTS: tuple[str, ...] = (
    "capability",
    "complexity_fit",
    "capacity",
    "locality",
    "cost",
    "total",
)


@dataclass(frozen=True)
class SelectionCandidate:
    """One considered agent with its score and, when rejected, the reason."""

    agent_id: str
    score: float = 0.0
    reasons: tuple[str, ...] = ()
    rejected_reason: str | None = None

    @property
    def rejected(self) -> bool:
        """True when the candidate failed a hard filter."""
        return self.rejected_reason is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "score": self.score,
            "reasons": list(self.reasons),
            "rejected_reason": self.rejected_reason,
        }


@dataclass(frozen=True)
class SelectionResult:
    """Outcome of one selection request."""

    selected: AgentProfile
    candidates: tuple[SelectionCandidate, ...] = ()
    explanation: str = ""
    score_breakdown: Mapping[str, Mapping[str, float]] = field(
        default_factory=dict
    )

    @property
    def selected_id(self) -> str:
        """Convenience accessor for the winning agent id."""
        return self.selected.agent_id

    def rejected_candidates(self) -> list[SelectionCandidate]:
        """Return only the hard-filtered candidates."""
        return [candidate for candidate in self.candidates if candidate.rejected]

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected.agent_id,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "explanation": self.explanation,
            "score_breakdown": {
                agent_id: dict(breakdown)
                for agent_id, breakdown in self.score_breakdown.items()
            },
        }


@dataclass(frozen=True)
class _Scored:
    """Internal ranked candidate (never leaves the module)."""

    profile: AgentProfile
    breakdown: Mapping[str, float]
    reasons: tuple[str, ...]

    @property
    def total(self) -> float:
        return float(self.breakdown["total"])


class AgentSelector:
    """Capability, permission and capacity aware selector.

    Args:
        directory: source of :class:`AgentProfile` objects and live load.
        config: optional selection policy object (reserved for future
            thresholds; accepted so the runtime can pass one configuration).
        governor: optional resource governor.  Three duck-typed forms are
            supported: a non-mutating probe named ``can_admit`` /
            ``check_admission`` / ``peek_admission``; a plain predicate
            callable; or an object exposing ``admit`` whose admission is
            consumed only for the winning candidate (callers release the slot).
        provider_registry: optional model-provider registry reference
            (reserved for provider-aware selection; not consulted here).
    """

    def __init__(
        self,
        directory: AgentDirectory,
        *,
        config: Any | None = None,
        governor: Any | None = None,
        provider_registry: Any | None = None,
    ) -> None:
        if not callable(getattr(directory, "all", None)):
            raise TypeError("directory must expose all().")
        if not callable(getattr(directory, "active_count", None)):
            raise TypeError("directory must expose active_count().")
        self._directory = directory
        self.config = config
        self._governor = governor
        self._provider_registry = provider_registry

    # -- public API --------------------------------------------------------- #
    def select(
        self,
        required_capabilities: Iterable[str] = (),
        *,
        required_tools: Iterable[str] = (),
        complexity: int = 1,
        required_permissions: Iterable[str] = (),
        exclude_agents: Iterable[str] = (),
        prefer_local: bool = False,
    ) -> SelectionResult:
        """Select exactly one eligible agent or raise.

        Raises:
            AgentSelectionError: when no registered agent passes every hard
                filter.  The message lists each rejection reason.
            ValueError: for malformed requirements (programmer error).
        """
        capabilities = _normalise_requirements(
            required_capabilities, "required_capabilities"
        )
        tools = _normalise_requirements(required_tools, "required_tools")
        permissions = _normalise_requirements(
            required_permissions, "required_permissions"
        )
        excluded = _normalise_requirements(exclude_agents, "exclude_agents")
        if isinstance(complexity, bool) or not isinstance(complexity, int):
            raise ValueError("complexity must be an integer >= 1.")
        if complexity < 1:
            raise ValueError("complexity must be an integer >= 1.")

        profiles = sorted(
            self._directory.all(), key=lambda profile: profile.agent_id
        )
        if not profiles:
            raise AgentSelectionError(
                "No agents are registered in the directory; "
                "selection cannot proceed."
            )

        probe = self._governor_probe()
        rejected: list[SelectionCandidate] = []
        scored: list[_Scored] = []

        for profile in profiles:
            reason = self._hard_filter_reason(
                profile, capabilities, tools, complexity, permissions, excluded
            )
            if reason is not None:
                rejected.append(
                    SelectionCandidate(
                        agent_id=profile.agent_id, rejected_reason=reason
                    )
                )
                continue

            try:
                active = self._directory.active_count(profile.agent_id)
            except AgentNotFoundError:
                # The directory changed under us: treat as unavailable.
                rejected.append(
                    SelectionCandidate(
                        agent_id=profile.agent_id,
                        rejected_reason="status:unavailable",
                    )
                )
                continue

            if probe is not None:
                denial = self._probe_governor(profile, probe)
                if denial is not None:
                    rejected.append(
                        SelectionCandidate(
                            agent_id=profile.agent_id,
                            rejected_reason=f"governor:{denial}",
                        )
                    )
                    continue

            breakdown = self._score(
                profile, capabilities, complexity, prefer_local, active
            )
            reasons = self._reasons(
                profile, capabilities, tools, complexity, prefer_local, active
            )
            scored.append(
                _Scored(profile=profile, breakdown=breakdown, reasons=reasons)
            )

        # Deterministic ranking: descending score, ascending agent id.
        scored.sort(key=lambda item: (-item.total, item.profile.agent_id))

        selected: _Scored | None = None
        admission_governor = self._admission_governor(probe)
        for entry in scored:
            if admission_governor is not None:
                denial = self._try_admission(entry.profile, admission_governor)
                if denial is not None:
                    rejected.append(
                        SelectionCandidate(
                            agent_id=entry.profile.agent_id,
                            rejected_reason=f"governor:{denial}",
                        )
                    )
                    continue
            selected = entry
            break

        if selected is None:
            raise self._selection_error(
                capabilities, tools, complexity, permissions, rejected
            )

        # Build the full candidate list: ranked eligible agents first, then
        # rejected agents ordered by agent id.
        rejected_reasons = {
            candidate.agent_id: candidate.rejected_reason
            for candidate in rejected
        }
        scored_ids = {entry.profile.agent_id for entry in scored}
        candidates: list[SelectionCandidate] = []
        breakdowns: dict[str, dict[str, float]] = {}
        for entry in scored:
            candidates.append(
                SelectionCandidate(
                    agent_id=entry.profile.agent_id,
                    score=round(entry.total, 6),
                    reasons=entry.reasons,
                    rejected_reason=rejected_reasons.get(entry.profile.agent_id),
                )
            )
            breakdowns[entry.profile.agent_id] = dict(entry.breakdown)
        for candidate in sorted(rejected, key=lambda item: item.agent_id):
            if candidate.agent_id in scored_ids:
                continue
            candidates.append(candidate)
            breakdowns.setdefault(candidate.agent_id, {"total": 0.0})

        return SelectionResult(
            selected=selected.profile,
            candidates=tuple(candidates),
            explanation=self._explain(
                selected, capabilities, tools, complexity, permissions, rejected
            ),
            score_breakdown=breakdowns,
        )

    # -- hard filters ------------------------------------------------------- #
    def _hard_filter_reason(
        self,
        profile: AgentProfile,
        capabilities: frozenset[str],
        tools: frozenset[str],
        complexity: int,
        permissions: frozenset[str],
        excluded: frozenset[str],
    ) -> str | None:
        """Return the first failing filter reason, or ``None`` when eligible."""
        if not profile.enabled:
            return "status:disabled"
        if profile.status is AgentStatus.DISABLED:
            return "status:disabled"
        if profile.status is AgentStatus.UNAVAILABLE:
            return "status:unavailable"

        missing_capabilities = profile.missing_capabilities(capabilities)
        if missing_capabilities:
            return f"capability_missing:{missing_capabilities[0]}"

        missing_tools = profile.missing_tools(tools)
        if missing_tools:
            return f"tool_missing:{missing_tools[0]}"

        effective = profile.effective_max_complexity(capabilities)
        if complexity > effective:
            return f"complexity:{complexity}>{effective}"

        missing_permissions = profile.missing_permissions(permissions)
        if missing_permissions:
            return f"permission_missing:{missing_permissions[0]}"

        try:
            active = self._directory.active_count(profile.agent_id)
        except AgentNotFoundError:
            return "status:unavailable"
        if active >= profile.max_concurrent_tasks:
            return f"capacity:{active}/{profile.max_concurrent_tasks}"

        if profile.agent_id in excluded:
            return "excluded"

        return None

    # -- scoring ------------------------------------------------------------ #
    @staticmethod
    def _score(
        profile: AgentProfile,
        capabilities: frozenset[str],
        complexity: int,
        prefer_local: bool,
        active: int,
    ) -> dict[str, float]:
        """Return the numeric score breakdown (higher total wins)."""
        if capabilities:
            specificity = sum(
                1.0
                + 0.05
                * min(len(profile.capabilities[name].tools), 5)
                for name in capabilities
            ) / len(capabilities)
        else:
            specificity = 0.0

        effective = profile.effective_max_complexity(capabilities)
        complexity_fit = 1.0 - (abs(effective - complexity) / max(effective, 1))

        capacity = (
            profile.max_concurrent_tasks - active
        ) / profile.max_concurrent_tasks

        locality = (
            1.0 if prefer_local and profile.locality == "local" else 0.0
        )

        cost = 1.0 / profile.cost_weight

        total = (
            SPECIFICITY_WEIGHT * specificity
            + COMPLEXITY_WEIGHT * complexity_fit
            + CAPACITY_WEIGHT * capacity
            + LOCALITY_WEIGHT * locality
            + COST_WEIGHT * cost
        )
        return {
            "capability": round(specificity, 6),
            "complexity_fit": round(complexity_fit, 6),
            "capacity": round(capacity, 6),
            "locality": round(locality, 6),
            "cost": round(cost, 6),
            "total": round(total, 6),
        }

    @staticmethod
    def _reasons(
        profile: AgentProfile,
        capabilities: frozenset[str],
        tools: frozenset[str],
        complexity: int,
        prefer_local: bool,
        active: int,
    ) -> tuple[str, ...]:
        """Human-readable scoring reasons for one eligible candidate."""
        reasons: list[str] = [f"status:{profile.status.value}"]
        if capabilities:
            reasons.append("capability:" + ",".join(sorted(capabilities)))
        if tools:
            reasons.append("tools:" + ",".join(sorted(tools)))
        reasons.append(
            "complexity:"
            f"{complexity}<={profile.effective_max_complexity(capabilities)}"
        )
        reasons.append(
            f"capacity:{active}/{profile.max_concurrent_tasks}"
        )
        locality = "local" if profile.locality == "local" else "remote"
        if prefer_local:
            locality += " (preferred)"
        reasons.append(f"locality:{locality}")
        reasons.append(f"cost_weight:{profile.cost_weight:g}")
        return tuple(reasons)

    # -- governor integration ----------------------------------------------- #
    def _governor_probe(self):
        """Return a non-mutating admission probe, or ``None``."""
        governor = self._governor
        if governor is None:
            return None
        for name in ("can_admit", "check_admission", "peek_admission"):
            method = getattr(governor, name, None)
            if callable(method):
                return method
        if callable(governor):
            return governor
        return None

    def _admission_governor(self, probe):
        """Return an ``admit``-only governor to consult for the winner."""
        if probe is not None or self._governor is None:
            return None
        admit = getattr(self._governor, "admit", None)
        return self._governor if callable(admit) else None

    def _probe_governor(self, profile: AgentProfile, probe) -> str | None:
        """Consult a non-mutating governor; return a denial reason or ``None``."""
        try:
            result = probe(profile.agent_id)
        except TypeError:
            result = probe(profile)
        return self._admission_denial(result)

    def _try_admission(self, profile: AgentProfile, governor) -> str | None:
        """Consume one governor admission; return a denial reason or ``None``."""
        return self._admission_denial(governor.admit(profile.agent_id))

    @staticmethod
    def _admission_denial(value: Any) -> str | None:
        """Interpret a governor result as admitted (``None``) or a reason."""
        if value is None:
            return None
        if isinstance(value, bool):
            return None if value else "insufficient_resources"
        if isinstance(value, Mapping):
            admitted = bool(value.get("admitted", False))
            reason = str(value.get("reason") or "insufficient_resources")
            return None if admitted else reason
        if isinstance(value, (tuple, list)) and value:
            admitted = bool(value[0])
            reason = (
                str(value[1])
                if len(value) > 1 and value[1]
                else "insufficient_resources"
            )
            return None if admitted else reason
        admitted = getattr(value, "admitted", None)
        if admitted is not None:
            reason = str(
                getattr(value, "reason", "") or "insufficient_resources"
            )
            return None if bool(admitted) else reason
        raise TypeError(
            "governor probe must return a bool, mapping, tuple or admission "
            "object with an 'admitted' attribute."
        )

    # -- explanation and errors --------------------------------------------- #
    @staticmethod
    def _explain(
        selected: _Scored,
        capabilities: frozenset[str],
        tools: frozenset[str],
        complexity: int,
        permissions: frozenset[str],
        rejected: list[SelectionCandidate],
    ) -> str:
        parts = [
            f"Selected agent {selected.profile.agent_id!r} with score "
            f"{selected.total:.6f}.",
            "Requirements: "
            f"capabilities={sorted(capabilities)}, "
            f"tools={sorted(tools)}, "
            f"complexity={complexity}, "
            f"permissions={sorted(permissions)}.",
            "Matched: " + "; ".join(selected.reasons) + ".",
        ]
        if rejected:
            summary = ", ".join(
                f"{candidate.agent_id} ({candidate.rejected_reason})"
                for candidate in sorted(
                    rejected, key=lambda item: item.agent_id
                )
            )
            parts.append(
                f"Rejected {len(rejected)} candidate(s): {summary}."
            )
        else:
            parts.append("No candidate was rejected.")
        return " ".join(parts)

    @staticmethod
    def _selection_error(
        capabilities: frozenset[str],
        tools: frozenset[str],
        complexity: int,
        permissions: frozenset[str],
        rejected: list[SelectionCandidate],
    ) -> AgentSelectionError:
        summary = ", ".join(
            f"{candidate.agent_id}={candidate.rejected_reason}"
            for candidate in sorted(rejected, key=lambda item: item.agent_id)
        )
        message = (
            "No eligible agent for requirements "
            f"(capabilities={sorted(capabilities)}, "
            f"tools={sorted(tools)}, "
            f"complexity={complexity}, "
            f"permissions={sorted(permissions)}); "
            f"rejected {len(rejected)} candidate(s): {summary}"
        )
        return AgentSelectionError(
            message,
            considered=len(rejected),
            rejections={
                candidate.agent_id: candidate.rejected_reason
                for candidate in rejected
            },
        )


def _normalise_requirements(values: Iterable[str], label: str) -> frozenset[str]:
    """Validate a requirement iterable into a ``frozenset`` of names."""
    if values is None:
        raise ValueError(f"{label} must be an iterable of non-empty strings.")
    if isinstance(values, (str, bytes)):
        raise ValueError(
            f"{label} must be an iterable of strings, not a bare string."
        )
    if not isinstance(values, Iterable):
        raise ValueError(f"{label} must be an iterable of non-empty strings.")
    collected: set[str] = set()
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"{label} entries must be non-empty strings, got {item!r}."
            )
        collected.add(item)
    return frozenset(collected)


__all__ = [
    "SCORE_COMPONENTS",
    "SPECIFICITY_WEIGHT",
    "COMPLEXITY_WEIGHT",
    "CAPACITY_WEIGHT",
    "LOCALITY_WEIGHT",
    "COST_WEIGHT",
    "SelectionCandidate",
    "SelectionResult",
    "AgentSelector",
]
