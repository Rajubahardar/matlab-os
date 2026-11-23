"""Provider-backed model registry adapter for Phase 13.

This adapter exposes the existing Phase 9 :class:`ProviderRegistry` as a
provider-independent model catalog for the universal intelligence layer.  It does
not replace the provider registry; it reads from it and presents a stable catalog
and selection surface to :class:`SharedIntelligence`.

The first vertical slice uses deterministic mock providers; real provider backends
(Ollama, remote) are wired by the existing Phase 9 configuration path.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ..model_registry.registry import ModelProfile, ModelRegistry, UnknownModelError
from .provider_catalog import ProviderCatalog


class ProviderModelRegistry(ModelRegistry):
    """A :class:`ModelRegistry` view over an existing provider catalog.

    Profiles in this registry are derived from provider metadata rather than an
    independent persistent catalog.  Selection delegates to the provider catalog's
    provider-selection logic.
    """

    def __init__(self, catalog: ProviderCatalog) -> None:
        super().__init__()
        self._catalog = catalog
        self._routing_log: list[Any] = []
        self._build_profiles()

    def _build_profiles(self) -> None:
        for provider in self._catalog.providers:
            profile = _provider_to_profile(provider, self._catalog)
            super().register(profile)

    def refresh(self) -> None:
        """Refresh profiles from the current provider catalog state."""
        self._models.clear()
        self._profiles.clear()
        self._routing_log.clear()
        self._build_profiles()

    def select_model(
        self,
        *,
        capabilities: list[str],
        prefer_local: bool = True,
        required_context_tokens: Optional[int] = None,
        context: Optional[Any] = None,
    ) -> Optional[Mapping[str, Any]]:
        chosen = self._catalog.select_provider(
            capabilities=capabilities,
            prefer_local=prefer_local,
        )
        if chosen is None:
            return None
        return _provider_to_profile(chosen, self._catalog).to_dict()

    def select_with_fallback(
        self,
        *,
        capabilities: list[str],
        prefer_local: bool = True,
        required_context_tokens: Optional[int] = None,
        context: Optional[Any] = None,
        attempt: "Callable[[Any], Mapping[str, Any]]",
    ) -> Mapping[str, Any]:
        from .registry import UnknownModelError

        candidates = [
            p for p in self._catalog.providers if _has_capabilities(p, set(capabilities))
        ]
        if not candidates:
            raise UnknownModelError(
                "No eligible provider satisfies the requested capabilities."
            )
        if prefer_local:
            local = [p for p in candidates if getattr(p, "locality", "") == "local"]
            if local:
                candidates = local
        last: BaseException | None = None
        for provider in candidates:
            try:
                return attempt(provider)
            except BaseException as exc:  # noqa: BLE001
                last = exc
                continue
        if last is None:
            raise UnknownModelError("No eligible provider found.")
        raise last

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
        from datetime import datetime, timezone

        self._routing_log.append(
            {
                "task_id": task_id or "unknown",
                "requested_capabilities": list(requested_capabilities or []),
                "selected_model": selected_model,
                "provider_id": provider_id,
                "locality": locality,
                "reason": reason,
                "selected_at": datetime.now(timezone.utc).isoformat(),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "estimated_cost_usd": estimated_cost_usd,
                "outcome": outcome,
            }
        )

    def routing_log(self, *, limit: int = 50) -> list[Mapping[str, Any]]:
        entries = (
            list(self._routing_log[-limit:]) if limit and limit > 0 else list(self._routing_log)
        )
        entries.reverse()
        return [dict(r) for r in entries]

    def routing_summary(self) -> Mapping[str, Any]:
        log = self._routing_log
        total = len(log)
        if total == 0:
            return {"total_decisions": 0, "by_locality": {}, "by_outcome": {}}
        by_locality: dict[str, int] = {}
        by_outcome: dict[str, int] = {}
        total_cost: float = 0.0
        cost_known = False
        for record in log:
            by_locality[record["locality"]] = by_locality.get(record["locality"], 0) + 1
            by_outcome[record["outcome"]] = by_outcome.get(record["outcome"], 0) + 1
            cost = record.get("estimated_cost_usd")
            if cost is not None:
                total_cost += float(cost)
                cost_known = True
        return {
            "total_decisions": total,
            "by_locality": by_locality,
            "by_outcome": by_outcome,
            "estimated_cost_usd": total_cost if cost_known else None,
        }


def _provider_to_profile(provider: Any, catalog: ProviderCatalog) -> ModelProfile:
    capabilities: dict[str, Any] = {}
    for cap in sorted(getattr(provider, "capabilities", ()) or ()):
        capabilities[str(cap)] = True

    return ModelProfile(
        name=_provider_model_name(provider),
        provider_id=provider.provider_id,
        locality=getattr(provider, "locality", "unknown"),
        capabilities=capabilities,
        context_tokens=getattr(provider, "default_context_tokens", None),
        notes=_provider_notes(provider),
    )


def _provider_model_name(provider: Any) -> str:
    return getattr(provider, "default_model", "") or provider.provider_id or "unknown"


def _provider_notes(provider: Any) -> str:
    name = getattr(provider, "default_model", "")
    if name:
        return f"default model: {name}"
    return "no default model configured"


class ProviderSelectionError(Exception):
    """Raised when provider-backed selection fails for a non-empty candidate set."""
