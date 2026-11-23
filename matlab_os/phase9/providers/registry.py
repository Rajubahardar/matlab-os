"""Deterministic provider registry with ordered fallback.

The registry is the only object allowed to decide *which* provider runs.  Its
rules are deliberately simple so that behaviour is reproducible:

* providers are held in configuration order;
* disabled providers are never constructed by :meth:`ProviderRegistry.from_config`,
  so a disabled remote provider can never be called;
* :meth:`ProviderRegistry.select` skips disabled/unavailable providers and
  raises :class:`ProviderUnavailableError` when nothing is eligible — it never
  invents a fallback;
* :meth:`ProviderRegistry.generate` records every attempt in the audit log and
  walks eligible providers in order, returning the first success and raising
  the last error when every candidate failed.

Audit details are constructed here and never include a credential: only the
provider id, model name, outcome, latency and typed error code are recorded.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable, Mapping

from ..audit import AuditLog, NullAuditLog
from ..config import Phase9Config, ProviderConfig
from ..errors import (
    ConfigurationError,
    Phase9Error,
    ProviderError,
    ProviderUnavailableError,
)
from ..security.redaction import redact
from .base import ModelProviderBase, ProviderRequest, ProviderResponse
from .deepseek import DeepSeekProvider
from .mock import MockProvider
from .ollama import OllamaProvider

#: Audit action recorded for every provider attempt.
AUDIT_ACTION = "provider.call"
#: Audit actor recorded for registry-driven calls.
AUDIT_ACTOR = "provider_registry"


class ProviderRegistry:
    """Ordered registry of model providers."""

    def __init__(
        self,
        config: Phase9Config,
        transport: Any | None = None,
        audit: AuditLog | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(config, Phase9Config):
            raise TypeError("config must be a Phase9Config instance.")
        self._config = config
        self._transport = transport
        self._clock: Callable[[], float] = clock if callable(clock) else time.time
        self._audit: AuditLog = audit if audit is not None else NullAuditLog()
        self._lock = threading.RLock()
        self._providers: dict[str, ModelProviderBase] = {}
        self._closed = False

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def from_config(
        cls,
        config: Phase9Config,
        *,
        transport: Any | None = None,
        audit: AuditLog | None = None,
    ) -> "ProviderRegistry":
        """Build exactly the providers described by *config*.

        Disabled providers are not constructed at all: this is what guarantees
        that a disabled DeepSeek never creates a network client and is never
        attempted by :meth:`generate`.
        """
        registry = cls(config, transport=transport, audit=audit)
        registry._build_from_config()
        return registry

    def _build_from_config(self) -> None:
        for provider_config in self._config.providers:
            if not isinstance(provider_config, ProviderConfig):
                raise ConfigurationError(
                    "providers entries must be ProviderConfig instances."
                )
            if not provider_config.enabled:
                continue
            if provider_config.provider_id in self._providers:
                raise ConfigurationError(
                    f"Duplicate provider id: {provider_config.provider_id}"
                )
            provider_config.validate()
            provider = self._create_provider(provider_config)
            self.register(provider)

    def _create_provider(self, provider_config: ProviderConfig) -> ModelProviderBase:
        kind = provider_config.kind
        if kind == "deepseek":
            return DeepSeekProvider(
                provider_config,
                transport=self._transport,
                audit=self._audit,
                clock=self._clock,
            )
        if kind == "ollama":
            return OllamaProvider(
                provider_config,
                runtime=None,
                audit=self._audit,
                clock=self._clock,
            )
        if kind == "mock":
            return MockProvider(
                provider_config,
                audit=self._audit,
                clock=self._clock,
            )
        raise ConfigurationError(
            f"Unsupported provider kind: {kind!r}",
            provider_id=provider_config.provider_id,
        )

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register(self, provider: ModelProviderBase) -> None:
        """Add *provider*; duplicate ids and wrong types are programmer errors."""
        if not isinstance(provider, ModelProviderBase):
            raise TypeError("provider must be a ModelProviderBase instance.")
        provider_id = provider.provider_id
        with self._lock:
            if provider_id in self._providers:
                raise ValueError(f"Provider already registered: {provider_id!r}")
            self._providers[provider_id] = provider

    def get(self, provider_id: str) -> ModelProviderBase:
        """Return the registered provider; raises ``KeyError`` when absent."""
        with self._lock:
            return self._providers[provider_id]

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #
    def enabled(self) -> list[ModelProviderBase]:
        """Configured providers in registration (configuration) order."""
        with self._lock:
            providers = list(self._providers.values())
        if self._closed:
            return []
        result: list[ModelProviderBase] = []
        for provider in providers:
            try:
                if provider.is_configured():
                    result.append(provider)
            except Exception:
                continue
        return result

    def health_all(self) -> dict[str, dict[str, Any]]:
        """Map ``provider_id -> health snapshot``; never raises."""
        with self._lock:
            providers = list(self._providers.values())
        snapshot: dict[str, dict[str, Any]] = {}
        for provider in providers:
            try:
                state = provider.health()
            except Exception:
                state = {
                    "provider_id": provider.provider_id,
                    "configured": False,
                    "available": False,
                    "detail": "health check failed",
                }
            snapshot[provider.provider_id] = state
        safe = redact(snapshot)
        return safe if isinstance(safe, dict) else snapshot

    # ------------------------------------------------------------------ #
    # Selection
    # ------------------------------------------------------------------ #
    def select(
        self,
        capability: str | None = None,
        *,
        prefer_local: bool = False,
        exclude: Iterable[str] = (),
    ) -> ModelProviderBase:
        """Return the first eligible provider in deterministic order."""
        if self._closed:
            raise self._no_provider_error(capability, prefer_local, exclude)
        eligible = self._eligible(
            capability=capability,
            prefer_local=prefer_local,
            exclude=exclude,
        )
        if not eligible:
            raise self._no_provider_error(capability, prefer_local, exclude)
        return eligible[0]

    def _eligible(
        self,
        *,
        capability: str | None,
        prefer_local: bool,
        exclude: Iterable[str] = (),
    ) -> list[ModelProviderBase]:
        excluded = {str(item) for item in exclude}
        with self._lock:
            providers = list(self._providers.values())
        eligible: list[ModelProviderBase] = []
        for provider in providers:
            if provider.provider_id in excluded:
                continue
            try:
                if not provider.is_configured() or not provider.is_available():
                    continue
            except Exception:
                # A provider that cannot report availability is not eligible.
                continue
            if capability is not None and capability not in provider.capabilities:
                continue
            eligible.append(provider)
        if prefer_local:
            local = [p for p in eligible if p.locality == "local"]
            remote = [p for p in eligible if p.locality != "local"]
            return local + remote
        return eligible

    @staticmethod
    def _no_provider_error(
        capability: str | None,
        prefer_local: bool,
        exclude: Iterable[str],
    ) -> ProviderUnavailableError:
        suffix = f" for capability {capability!r}" if capability else ""
        return ProviderUnavailableError(
            f"No eligible provider{suffix}.",
            capability=capability,
            prefer_local=bool(prefer_local),
            excluded=sorted({str(item) for item in exclude}),
        )

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #
    def generate(
        self,
        request: ProviderRequest,
        *,
        capability: str | None = None,
        prefer_local: bool = False,
        allow_fallback: bool = True,
    ) -> ProviderResponse:
        """Run *request* on the first eligible provider that succeeds."""
        if not isinstance(request, ProviderRequest):
            raise TypeError("request must be a ProviderRequest instance.")
        if self._closed:
            raise self._no_provider_error(capability, prefer_local, ())

        candidates = self._eligible(
            capability=capability,
            prefer_local=prefer_local,
        )
        if not candidates:
            raise self._no_provider_error(capability, prefer_local, ())
        if not allow_fallback:
            candidates = candidates[:1]

        correlation_id = self._correlation_id(request)
        last_error: ProviderError | None = None
        for provider in candidates:
            model = request.model or provider.default_model or ""
            started = self._clock()
            try:
                response = provider.generate(request)
            except Exception as exc:
                latency = max(0.0, float(self._clock() - started))
                error = self._coerce_error(provider, exc)
                self._record_attempt(
                    provider_id=provider.provider_id,
                    model=model,
                    outcome="error",
                    latency=latency,
                    capability=capability,
                    correlation_id=correlation_id,
                    error=error,
                )
                last_error = error
                continue
            latency = max(0.0, float(self._clock() - started))
            self._record_attempt(
                provider_id=provider.provider_id,
                model=response.model or model,
                outcome="success",
                latency=latency,
                capability=capability,
                correlation_id=correlation_id,
                error=None,
            )
            return response

        if last_error is None:  # pragma: no cover - defensive
            raise self._no_provider_error(capability, prefer_local, ())
        raise last_error

    # ------------------------------------------------------------------ #
    # Audit plumbing
    # ------------------------------------------------------------------ #
    def _record_attempt(
        self,
        *,
        provider_id: str,
        model: str,
        outcome: str,
        latency: float,
        capability: str | None,
        correlation_id: str | None,
        error: ProviderError | None,
    ) -> None:
        details: dict[str, Any] = {
            "provider_id": provider_id,
            "model": model,
            "outcome": outcome,
            "latency_seconds": round(float(latency), 6),
        }
        if capability:
            details["capability"] = capability
        if error is not None:
            details["error_code"] = getattr(error, "code", "provider_error")
            details["error_type"] = type(error).__name__
            details["retryable"] = bool(getattr(error, "retryable", False))
        # AuditLog redacts again before persisting; no credential is passed.
        self._audit.record(
            AUDIT_ACTION,
            AUDIT_ACTOR,
            outcome,
            details,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _correlation_id(request: ProviderRequest) -> str | None:
        metadata = getattr(request, "metadata", None)
        if isinstance(metadata, Mapping):
            value = metadata.get("correlation_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _coerce_error(
        provider: ModelProviderBase,
        exc: BaseException,
    ) -> ProviderError:
        if isinstance(exc, ProviderError):
            return exc
        if isinstance(exc, Phase9Error):
            return ProviderError(
                f"Provider {provider.provider_id!r} failed with "
                f"{type(exc).__name__}.",
                provider_id=provider.provider_id,
            )
        # Foreign exception text is never echoed: it could carry payload data.
        return ProviderError(
            f"Provider {provider.provider_id!r} raised an unexpected error.",
            provider_id=provider.provider_id,
            retryable=False,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """Close every provider; idempotent and never raises."""
        with self._lock:
            providers = list(self._providers.values())
            self._closed = True
        for provider in providers:
            try:
                provider.close()
            except Exception:  # pragma: no cover - defensive cleanup
                continue


__all__ = [
    "AUDIT_ACTION",
    "AUDIT_ACTOR",
    "ProviderRegistry",
]
