"""Provider catalog for Phase 13 universal intelligence.

The catalog provides a provider-independent read view over a set of model
providers and a simple capability-based selection rule.  It is intentionally
smaller than the full Phase 9 ProviderRegistry: it does not own lifecycle,
configuration parsing, audit, transports, or network access.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional


class ProviderCatalog:
    """A provider-independent catalog for selection and profiling."""

    def __init__(self, providers: Iterable[Any] | None = None) -> None:
        self._providers: list[Any] = []
        self.extend(providers or ())

    def extend(self, providers: Iterable[Any]) -> None:
        for provider in providers:
            self.register(provider)

    def register(self, provider: Any) -> None:
        if not _is_provider(provider):
            raise TypeError("Expected a provider-like object.")
        self._providers.append(provider)

    @property
    def providers(self) -> list[Any]:
        return list(self._providers)

    def select_provider(
        self,
        *,
        capabilities: list[str],
        prefer_local: bool = True,
    ) -> Optional[Any]:
        required = set(capabilities)
        candidates = self._candidates_for(required)
        if not candidates:
            return None
        if prefer_local:
            local = [p for p in candidates if getattr(p, "locality", "") == "local"]
            if local:
                candidates = local
        return candidates[0]

    def select_with_fallback(
        self,
        *,
        capabilities: list[str],
        prefer_local: bool = True,
        required_context_tokens: Optional[int] = None,
        context: Optional[Any] = None,
        attempt: "Callable[[Any], Any]",
    ) -> Any:
        """Select and attempt a provider, falling back through eligible providers on failure.

        Eligible providers are ordered by the same rules as
        :meth:`select_provider`: required capabilities first, then local preference.
        """
        candidates = self._candidates_for(set(capabilities))
        if not candidates:
            from .registry import UnknownModelError

            raise UnknownModelError(
                "No eligible provider satisfies the requested capabilities."
            )
        if prefer_local:
            local = [p for p in candidates if getattr(p, "locality", "") == "local"]
            if local:
                candidates = local
        last: BaseException | None = None
        for candidate in candidates:
            try:
                return attempt(candidate)
            except BaseException as exc:  # noqa: BLE001
                last = exc
                continue
        if last is None:
            from .registry import UnknownModelError

            raise UnknownModelError("No eligible provider found.")
        raise last

    def _candidates_for(self, required: set[str]) -> list[Any]:
        candidates = [p for p in self._providers if _has_capabilities(p, required)]
        if not candidates:
            return []
        return candidates


def _is_provider(provider: Any) -> bool:
    return bool(
        provider is not None
        and getattr(provider, "provider_id", None)
        and callable(getattr(provider, "is_available", None))
    )


def _has_capabilities(provider: Any, required: set[str]) -> bool:
    caps = getattr(provider, "capabilities", None)
    if caps is None:
        return False
    try:
        cap_set = set(caps)
    except Exception:
        return False
    return required.issubset(cap_set)
