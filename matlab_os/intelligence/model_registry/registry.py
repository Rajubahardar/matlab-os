"""Provider-independent model registry for Phase 13.

The registry stores model entries with capability profiles and supports
selection by requested capabilities and local preference, with context-window
and cost awareness and a simple fallback policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UnknownModelError(Exception):
    """Raised when a model name is not known to the registry."""


class SelectionContext:
    """Request metadata used for cost/context-aware selection and telemetry.

    This is intentionally small: the intelligence layer does not need the full
    provider request object to make a routing decision, only enough to estimate
    context and cost.
    """

    def __init__(
        self,
        *,
        requested_capabilities: list[str] | None = None,
        prefer_local: bool = True,
        estimated_prompt_tokens: Optional[int] = None,
        estimated_completion_tokens: Optional[int] = None,
        required_context_tokens: Optional[int] = None,
        task_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        self.requested_capabilities = list(requested_capabilities or [])
        self.prefer_local = bool(prefer_local)
        self.estimated_prompt_tokens = (
            int(estimated_prompt_tokens) if estimated_prompt_tokens is not None else None
        )
        self.estimated_completion_tokens = (
            int(estimated_completion_tokens)
            if estimated_completion_tokens is not None
            else None
        )
        self.required_context_tokens = (
            int(required_context_tokens)
            if required_context_tokens is not None
            else None
        )
        self.task_id = task_id
        self.correlation_id = correlation_id


@dataclass(frozen=True)
class RoutingRecord:
    """One routing decision recorded for observability."""

    task_id: str
    requested_capabilities: list[str]
    selected_model: str
    provider_id: str
    locality: str
    reason: str
    selected_at: str
    prompt_tokens: Optional[int] | None = None
    completion_tokens: Optional[int] | None = None
    estimated_cost_usd: Optional[float] | None = None
    outcome: str = "selected"


class ModelProfile:
    """A provider-independent model entry with capability metadata.

    Parameters
    ----------
    name:
        Stable model identifier.
    provider_id:
        Provider/system that supplies the model.
    locality:
        ``"local"``, ``"remote"``, or other descriptive label.
    capabilities:
        Capability tags the model can serve.
    context_tokens:
        Approximate context window, if known.
    input_cost_per_1k_tokens:
        Approximate input cost in USD per 1,000 tokens, if known.
    output_cost_per_1k_tokens:
        Approximate output cost in USD per 1,000 tokens, if known.
    max_output_tokens:
        Approximate max generation length the model can produce, if known.
    """

    def __init__(
        self,
        *,
        name: str,
        provider_id: str,
        locality: str = "unknown",
        capabilities: Optional[Mapping[str, Any]] = None,
        context_tokens: Optional[int] = None,
        input_cost_per_1k_tokens: Optional[float] = None,
        output_cost_per_1k_tokens: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name is required.")
        self.name = name.strip()
        self.provider_id = provider_id
        self.locality = locality
        self.capabilities = dict(capabilities or {})
        self.context_tokens = context_tokens
        self.input_cost_per_1k_tokens = (
            float(input_cost_per_1k_tokens)
            if input_cost_per_1k_tokens is not None
            else None
        )
        self.output_cost_per_1k_tokens = (
            float(output_cost_per_1k_tokens)
            if output_cost_per_1k_tokens is not None
            else None
        )
        self.max_output_tokens = (
            int(max_output_tokens) if max_output_tokens is not None else None
        )
        self.notes = notes
        self.created_at = _utc_now()
        self.updated_at = self.created_at

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "name": self.name,
            "provider_id": self.provider_id,
            "locality": self.locality,
            "capabilities": dict(self.capabilities),
            "context_tokens": self.context_tokens,
            "input_cost_per_1k_tokens": self.input_cost_per_1k_tokens,
            "output_cost_per_1k_tokens": self.output_cost_per_1k_tokens,
            "max_output_tokens": self.max_output_tokens,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def estimate_cost_for_completion(
        self,
        *,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
    ) -> Optional[float]:
        """Return an approximate USD cost for one completion through this model."""
        prompt = int(prompt_tokens) if prompt_tokens is not None else 0
        completion = int(completion_tokens) if completion_tokens is not None else 0
        if prompt < 0 or completion < 0:
            return None
        in_cost = self.input_cost_per_1k_tokens
        out_cost = self.output_cost_per_1k_tokens
        if in_cost is None and out_cost is None:
            return None
        total = 0.0
        if in_cost is not None and prompt:
            total += in_cost * prompt / 1000.0
        if out_cost is not None and completion:
            total += out_cost * completion / 1000.0
        return total if total else None


class ModelRegistry:
    """Provider-independent model catalog with capability profiling.

    This is the Phase 13 registry surface reused by the SharedIntelligence facade
    and later routing policy.
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelProfile] = {}
        self._profiles: dict[str, set[str]] = {}
        self._routing_log: list[RoutingRecord] = []

    # ------------------------------------------------------------------ #
    # Catalog
    # ------------------------------------------------------------------ #

    def register(self, profile: ModelProfile) -> None:
        if not isinstance(profile, ModelProfile):
            raise TypeError("Expected a ModelProfile.")
        name = profile.name
        self._models[name] = profile
        self._profiles.setdefault(profile.provider_id, set()).add(name)
        profile.updated_at = _utc_now()

    def unregister(self, name: str) -> bool:
        profile = self._models.pop(name, None)
        if profile is None:
            return False
        for names in self._profiles.values():
            names.discard(name)
        return True

    def list_models(self) -> list[Mapping[str, Any]]:
        return [self.get_model(name).to_dict() for name in sorted(self._models)]

    def get_model(self, name: str) -> ModelProfile:
        profile = self._models.get(name)
        if profile is None:
            raise UnknownModelError(f"Unknown model: {name!r}.")
        return profile

    # ------------------------------------------------------------------ #
    # Profiles / providers
    # ------------------------------------------------------------------ #

    def profile_exists(self, profile_name: str) -> bool:
        """Return True when a provider/profile name is known."""
        return profile_name in self._profiles

    def list_provider_model_names(self, provider_id: str) -> list[str]:
        return sorted(self._profiles.get(provider_id, ()))

    # ------------------------------------------------------------------ #
    # Capability selection
    # ------------------------------------------------------------------ #

    def select_model(
        self,
        *,
        capabilities: list[str],
        prefer_local: bool = True,
        required_context_tokens: Optional[int] = None,
        context: Optional[SelectionContext] = None,
    ) -> Optional[Mapping[str, Any]]:
        """Select the first model that satisfies the requested capabilities.

        When ``prefer_local`` is true and several models qualify, local models are
        preferred. When no model satisfies the request, ``None`` is returned.

        This is the simple single-candidate selection path. Use
        :meth:`select_with_fallback` when a routing attempt should try the next
        eligible model on failure.
        """
        if not isinstance(capabilities, list):
            raise TypeError("capabilities must be a list.")
        candidates = self._candidates_for(
            capabilities=capabilities,
            prefer_local=prefer_local,
            required_context_tokens=required_context_tokens,
            context=context,
        )
        if not candidates:
            return None
        return candidates[0].to_dict()

    def select_with_fallback(
        self,
        *,
        capabilities: list[str],
        prefer_local: bool = True,
        required_context_tokens: Optional[int] = None,
        context: Optional[SelectionContext] = None,
        attempt: Callable[[ModelProfile], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Select and attempt a model, falling back through eligible models on failure.

        Eligible models are ordered by the same rules as
        :meth:`select_model`: required capabilities first, then context-window
        filtering, then local preference. The first model whose
        ``attempt(profile)`` call succeeds is returned.

        When no eligible model exists, raises :class:`UnknownModelError`.
        When every eligible model fails, raises the last failure.
        """
        candidates = self._candidates_for(
            capabilities=capabilities,
            prefer_local=prefer_local,
            required_context_tokens=required_context_tokens,
            context=context,
        )
        if not candidates:
            raise UnknownModelError(
                "No eligible model satisfies the requested capabilities."
            )
        last: BaseException | None = None
        for candidate in candidates:
            try:
                return attempt(candidate)
            except BaseException as exc:  # noqa: BLE001
                last = exc
                continue
        if last is None:
            raise UnknownModelError("No eligible model found.")
        raise last

    # ------------------------------------------------------------------ #
    # Routing utilization log
    # ------------------------------------------------------------------ #

    def record_routing(
        self,
        *,
        task_id: str,
        requested_capabilities: list[str],
        selected_model: str,
        provider_id: str,
        locality: str,
        reason: str,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        estimated_cost_usd: Optional[float] = None,
        outcome: str = "selected",
    ) -> None:
        """Record one routing decision for observability."""
        record = RoutingRecord(
            task_id=task_id or "unknown",
            requested_capabilities=list(requested_capabilities or []),
            selected_model=selected_model,
            provider_id=provider_id,
            locality=locality,
            reason=reason,
            selected_at=_utc_now(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=estimated_cost_usd,
            outcome=outcome,
        )
        self._routing_log.append(record)

    def routing_log(self, *, limit: int = 50) -> list[Mapping[str, Any]]:
        """Return recent routing records, newest first."""
        entries = (
            list(self._routing_log[-limit:])
            if limit and limit > 0
            else list(self._routing_log)
        )
        entries.reverse()
        return [r.__dict__ for r in entries]

    def routing_summary(self) -> Mapping[str, Any]:
        """Return a compact summary of recent routing activity."""
        log = self._routing_log
        total = len(log)
        if total == 0:
            return {"total_decisions": 0, "by_locality": {}, "by_outcome": {}}
        by_locality: dict[str, int] = {}
        by_outcome: dict[str, int] = {}
        total_cost: float = 0.0
        cost_known = False
        for record in log:
            by_locality[record.locality] = (
                by_locality.get(record.locality, 0) + 1
            )
            by_outcome[record.outcome] = by_outcome.get(record.outcome, 0) + 1
            cost = record.estimated_cost_usd
            if cost is not None:
                total_cost += float(cost)
                cost_known = True
        return {
            "total_decisions": total,
            "by_locality": by_locality,
            "by_outcome": by_outcome,
            "estimated_cost_usd": total_cost if cost_known else None,
        }

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #

    def health_check(self) -> Mapping[str, Any]:
        return {
            "models": len(self._models),
            "providers": len(self._profiles),
            "by_locality": self._counts_by("locality"),
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _candidates_for(
        self,
        *,
        capabilities: list[str],
        prefer_local: bool,
        required_context_tokens: Optional[int],
        context: Optional[SelectionContext],
    ) -> list[ModelProfile]:
        required = set(capabilities)

        effective_context = required_context_tokens
        if context is not None and context.required_context_tokens is not None:
            effective_context = context.required_context_tokens

        effective_prefer_local = prefer_local
        if context is not None:
            effective_prefer_local = bool(context.prefer_local)

        candidates: list[ModelProfile] = []
        for profile in self._models.values():
            if not required.issubset(set(profile.capabilities)):
                continue
            if (
                effective_context is not None
                and profile.context_tokens is not None
                and profile.context_tokens < effective_context
            ):
                continue
            candidates.append(profile)

        if not candidates:
            return []

        if effective_prefer_local:
            local = [p for p in candidates if p.locality == "local"]
            if local:
                candidates = local

        return candidates

    def _counts_by(self, key: str) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for profile in self._models.values():
            value = getattr(profile, key, None) or "unknown"
            counts[str(value)] = counts.get(str(value), 0) + 1
        return counts
