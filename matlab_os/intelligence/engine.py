"""Universal Model Intelligence facade for Local AI OS.

This is the Phase 13 entry point for provider-independent model intelligence.

Important distinction
--------------------
The historical ``SharedIntelligence`` in earlier backups was a *skill-candidate
lifecycle + experience journal* class (create/list/validate/approve/publish
candidates, record experiences, execute through an approval-controlled dispatcher).
That earlier component is **not** what this phase is building, and it is **not**
reimplemented here.

The current ``SharedIntelligence`` is the universal model-intelligence facade:
registry-aware model selection, capability profiling, and routing context.  The
registry, providers, and routing policy are separate modules; this facade coordinates
them and exposes a stable surface that consumers can depend on.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


class SharedIntelligence:
    """Provider-independent model intelligence facade.

    This facade is the observable front door for model selection and capability
    profiling in Phase 13.  It delegates to a provider-backed catalog / registry
    rather than implementing provider-specific behavior itself.

    The facade intentionally accepts an already-built provider catalog or registry
    so that Phase 13 can reuse the existing Phase 9 provider layer instead of
    inventing a parallel registry lifecycle.
    """

    def __init__(
        self,
        *,
        registry: Optional[Any] = None,
        catalog: Optional[Any] = None,
        clock=None,
    ) -> None:
        self._registry = registry
        self._catalog = catalog
        self._clock = clock

    # ------------------------------------------------------------------ #
    # Core facade surface
    # ------------------------------------------------------------------ #

    def identify(self) -> Mapping[str, Any]:
        """Return a stable description of this intelligence layer."""
        sources: list[str] = []
        if self._registry is not None:
            sources.append("registry")
        if self._catalog is not None:
            sources.append("catalog")
        return {
            "layer": "universal_model_intelligence",
            "phase": 13,
            "version": "0.1.0-dev",
            "sources": sources,
        }

    def model_catalog(self) -> list[Mapping[str, Any]]:
        """Enumerate known model profiles from the registry/catalog, if available."""
        source: Any | None = None
        if self._registry is not None:
            source = self._registry
        elif self._catalog is not None:
            source = self._catalog
        if source is None:
            return []
        try:
            names = getattr(source, "list_models", None)
            if callable(names):
                return list(names())
        except Exception:
            return []
        candidates = getattr(source, "providers", None)
        if callable(candidates):
            return [_profile_for(c) for c in candidates() if _is_provider_like(c)]
        if isinstance(candidates, list):
            return [_profile_for(c) for c in candidates if _is_provider_like(c)]
        return []

    def select_model(
        self,
        *,
        capabilities: list[str],
        prefer_local: bool = True,
        required_context_tokens: Optional[int] = None,
        context: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Mapping[str, Any]]:
        """Select an appropriate model for the requested capabilities.

        When a provider-backed catalog is present, this is the unified routing
        surface for the system: consumers request capabilities and receive an
        appropriate model through one observable path.

        ``context`` is an optional routing context carrying estimated prompt/
        completion tokens and a task/correlation id; it is passed through to the
        registry when the registry supports it.
        """
        if self._catalog is not None:
            try:
                choose = getattr(self._catalog, "select_provider", None)
                if callable(choose):
                    chosen = choose(
                        capabilities=capabilities,
                        prefer_local=prefer_local,
                    )
                    if chosen is not None:
                        return {
                            "name": _provider_model_name(chosen),
                            "provider_id": getattr(chosen, "provider_id", ""),
                            "locality": getattr(chosen, "locality", "unknown"),
                            "capabilities": _provider_capabilities(chosen),
                            "source": "catalog",
                        }
            except Exception:
                return None

        registry = self._registry
        if registry is None:
            return None
        try:
            choose = getattr(registry, "select_model", None)
            if callable(choose):
                return choose(
                    capabilities=capabilities,
                    prefer_local=prefer_local,
                    required_context_tokens=required_context_tokens,
                    context=context,
                )
        except Exception:
            return None
        return None

    def select_model_with_fallback(
        self,
        *,
        capabilities: list[str],
        prefer_local: bool = True,
        required_context_tokens: Optional[int] = None,
        context: Optional[Mapping[str, Any]] = None,
        attempt,
    ) -> Mapping[str, Any]:
        """Select and attempt a model, falling back through eligible models on failure.

        This is the unified fallback path for the intelligence layer: when the
        chosen model cannot serve a request, the system tries the next eligible
        model in deterministic order rather than failing immediately.

        ``attempt`` must accept a model-or-provider description and return a
        mapping for the successful case. The first successful attempt is
        returned.
        """
        catalog = self._catalog
        registry = self._registry

        catalog_has = (
            catalog is not None
            and callable(getattr(catalog, "select_with_fallback", None))
        )
        registry_has = (
            registry is not None
            and callable(getattr(registry, "select_with_fallback", None))
        )

        if not catalog_has and not registry_has:
            raise RuntimeError("No routing source supports fallback selection.")

        kwargs = {
            "capabilities": capabilities,
            "prefer_local": prefer_local,
            "required_context_tokens": required_context_tokens,
            "context": context,
            "attempt": attempt,
        }

        if catalog_has:
            try:
                return catalog.select_with_fallback(**kwargs)
            except Exception:
                if not registry_has:
                    raise

        if registry_has:
            try:
                return registry.select_with_fallback(**kwargs)
            except Exception:
                raise

        raise RuntimeError("No routing source supports fallback selection.")

    def select_provider(
        self,
        *,
        capabilities: list[str],
        prefer_local: bool = True,
    ) -> Optional[Any]:
        """Select a real provider object for the requested capabilities.

        This is the Phase 13 routing path that later phases delegate execution to.
        It prefers the provider-backed catalog when present.
        """
        if self._catalog is not None:
            try:
                choose = getattr(self._catalog, "select_provider", None)
                if callable(choose):
                    return choose(
                        capabilities=capabilities,
                        prefer_local=prefer_local,
                    )
            except Exception:
                return None
        return None

    def profile_exists(self, profile_name: str) -> bool:
        """Return True when a provider/profile with the given name is known."""
        source: Any | None = None
        if self._registry is not None:
            source = self._registry
        elif self._catalog is not None:
            source = self._catalog
        if source is None:
            return False
        try:
            probe = getattr(source, "profile_exists", None)
            if callable(probe):
                return bool(probe(profile_name))
        except Exception:
            return False
        return False

    def routing_summary(self) -> Mapping[str, Any]:
        """Return a compact summary of recent routing activity, if observable."""
        source: Any | None = None
        if self._registry is not None:
            source = self._registry
        elif self._catalog is not None:
            source = self._catalog
        if source is None:
            return {"layer": "universal_model_intelligence", "total_decisions": 0}
        probe = getattr(source, "routing_summary", None)
        if callable(probe):
            try:
                return dict(probe())
            except Exception:
                return {"layer": "universal_model_intelligence", "error": "routing summary unavailable"}
        return {"layer": "universal_model_intelligence", "total_decisions": 0}

    # ------------------------------------------------------------------ #
    # Diagnostics
    # ------------------------------------------------------------------ #

    def health(self) -> Mapping[str, Any]:
        """Return an observable health summary for the intelligence layer."""
        registry_status: Mapping[str, Any] = {"available": False}
        if self._registry is not None:
            try:
                check = getattr(self._registry, "health_check", None)
                if callable(check):
                    registry_status = check()
            except Exception as exc:  # noqa: BLE001 - diagnostics must not raise
                registry_status = {"available": False, "error": str(exc)[:500]}
        catalog_status: Mapping[str, Any] = {"available": False}
        if self._catalog is not None:
            try:
                count = getattr(self._catalog, "providers", None)
                if callable(count):
                    count = count()
                catalog_status = {"available": True, "providers": len(list(count or ()))}
            except Exception as exc:  # noqa: BLE001
                catalog_status = {"available": False, "error": str(exc)[:500]}
        return {
            "layer": "universal_model_intelligence",
            "registry": registry_status,
            "catalog": catalog_status,
        }


def _is_provider_like(provider: Any) -> bool:
    return bool(
        provider is not None
        and getattr(provider, "provider_id", None)
        and callable(getattr(provider, "is_available", None))
    )


def _provider_model_name(provider: Any) -> str:
    return getattr(provider, "default_model", "") or provider.provider_id or "unknown"


def _provider_capabilities(provider: Any) -> list[str]:
    caps = getattr(provider, "capabilities", ()) or ()
    try:
        return sorted(set(caps))
    except Exception:
        return []
