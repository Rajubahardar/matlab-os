"""Local Ollama provider.

This module is a thin Phase 9 adapter around the pre-existing Local AI OS
runtime adapter:

    matlab_os.intelligence.model_registry.runtime_adapter.OllamaRuntimeAdapter

The import is **read-only**: no file in ``matlab_os/`` is modified, and the
import is performed lazily so that importing the Phase 9 providers package
never requires the main project or performs any I/O.

Availability probing is defensive: a local service that is down (or an adapter
that raises) must report ``False`` from :meth:`OllamaProvider.is_available`
instead of propagating an exception.
"""

from __future__ import annotations

import socket
import time
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping

from ..errors import (
    ProviderError,
    ProviderNotConfiguredError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ..security.redaction import scrub_text
from .base import (
    ModelProviderBase,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
    coerce_int,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..audit import AuditLog
    from ..config import ProviderConfig

#: Default endpoint of the isolated Local AI OS Ollama service.
DEFAULT_HOST = "http://127.0.0.1:11435"


class OllamaProvider(ModelProviderBase):
    """Local inference provider backed by the existing Ollama adapter."""

    provider_id = "ollama_local"
    capabilities = frozenset({"text_generation", "local_inference"})
    locality = "local"
    default_model = ""

    def __init__(
        self,
        config: "ProviderConfig",
        runtime: Any | None = None,
        audit: "AuditLog | None" = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(audit=audit, clock=clock)
        if config is None:
            raise ValueError("config is required.")
        self._config = config
        if config.model:
            self.default_model = config.model
        self._runtime = runtime if runtime is not None else self._default_runtime()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _default_runtime(self) -> Any | None:
        """Build the reused adapter; never performs I/O and never raises."""
        try:  # read-only reuse of the stable main-project layer
            from matlab_os.intelligence.model_registry.runtime_adapter import (  # noqa: E501
                OllamaRuntimeAdapter,
            )
        except Exception:  # pragma: no cover - optional reuse
            return None
        try:
            host = (self._config.endpoint or DEFAULT_HOST).rstrip("/")
            timeout = max(1, int(round(float(self._config.timeout_seconds))))
            return OllamaRuntimeAdapter(host=host or DEFAULT_HOST, timeout=timeout)
        except Exception:  # pragma: no cover - defensive
            return None

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def is_configured(self) -> bool:
        if self.closed:
            return False
        if not self._config.enabled:
            return False
        return self._runtime is not None

    def is_available(self) -> bool:
        """Return False (never raise) when the local service is down."""
        if not self.is_configured():
            return False
        runtime = self._runtime
        if runtime is None:  # pragma: no cover - guarded by is_configured
            return False
        try:
            return bool(runtime.is_available())
        except Exception:
            return False

    def _health_detail(self, configured: bool, available: bool) -> str:
        if not self._config.enabled:
            return "disabled by configuration"
        if not configured:
            return "runtime adapter unavailable"
        return "ready" if available else "local Ollama service is not reachable"

    def _repr_fields(self) -> Mapping[str, Any]:
        return {
            "endpoint": self._config.endpoint or DEFAULT_HOST,
            "model": self.default_model,
        }

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #
    def generate(self, request: ProviderRequest) -> ProviderResponse:
        request = self._validate_request(request)
        self._ensure_open()
        if not self._config.enabled:
            raise ProviderNotConfiguredError(
                "Ollama provider is disabled by configuration.",
                provider_id=self.provider_id,
            )
        runtime = self._runtime
        if runtime is None:
            raise ProviderUnavailableError(
                "Ollama runtime adapter is not available.",
                provider_id=self.provider_id,
            )
        model = request.model or self.default_model
        if not model:
            raise ProviderUnavailableError(
                "No Ollama model is configured or supplied with the request.",
                provider_id=self.provider_id,
            )

        options = {
            "temperature": float(request.temperature),
            "num_predict": int(request.max_tokens),
        }
        started = self._clock()
        try:
            result = runtime.generate(model, request.prompt, options)
        except Exception as exc:
            raise self._classify_runtime_error(exc) from None
        return self._build_response(result, request, model, self._elapsed(started))

    def stream(self, request: ProviderRequest) -> Iterator[str]:
        # The reused adapter is non-streaming; emit the completed text in
        # deterministic chunks so callers get a uniform streaming interface.
        response = self.generate(request)
        return self._iter_chunks(response.text)

    @staticmethod
    def _iter_chunks(text: str, size: int = 64) -> Iterator[str]:
        for index in range(0, len(text), size):
            yield text[index:index + size]

    # ------------------------------------------------------------------ #
    # Conversion helpers
    # ------------------------------------------------------------------ #
    def _classify_runtime_error(self, exc: BaseException) -> ProviderError:
        if isinstance(exc, ProviderError):
            return exc
        message = scrub_text(str(exc))[:300]
        lowered = message.lower()
        if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in lowered:
            return ProviderTimeoutError(
                f"Ollama request timed out: {message}",
                provider_id=self.provider_id,
            )
        if (
            "not found" in lowered
            or "no such model" in lowered
            or " 404" in lowered
            or "404:" in lowered
        ):
            return ProviderUnavailableError(
                f"Ollama model is unavailable: {message}",
                provider_id=self.provider_id,
            )
        if "connection" in lowered or "refused" in lowered or "unreachable" in lowered:
            return ProviderUnavailableError(
                f"Ollama service is unreachable: {message}",
                provider_id=self.provider_id,
            )
        return ProviderError(
            f"Ollama generation failed: {message}",
            provider_id=self.provider_id,
            retryable=False,
        )

    def _build_response(
        self,
        result: Any,
        request: ProviderRequest,
        model: str,
        latency: float,
    ) -> ProviderResponse:
        if not isinstance(result, Mapping):
            raise ProviderResponseError(
                "Ollama adapter returned a non-mapping result.",
                provider_id=self.provider_id,
            )
        text = result.get("response")
        if not isinstance(text, str):
            raise ProviderResponseError(
                "Ollama adapter result did not contain a text 'response' field.",
                provider_id=self.provider_id,
            )
        prompt_tokens = coerce_int(result.get("prompt_eval_count"))
        completion_tokens = coerce_int(result.get("eval_count"))
        finish_reason = result.get("done_reason")
        if not isinstance(finish_reason, str) or not finish_reason:
            finish_reason = "stop"
        response_model = result.get("model")
        if not isinstance(response_model, str) or not response_model:
            response_model = request.model or model
        return ProviderResponse(
            provider_id=self.provider_id,
            model=response_model,
            text=text,
            usage=ProviderUsage(
                prompt_tokens=max(0, prompt_tokens),
                completion_tokens=max(0, completion_tokens),
                total_tokens=max(0, prompt_tokens) + max(0, completion_tokens),
            ),
            latency_seconds=max(0.0, latency),
            finish_reason=finish_reason,
            streamed=False,
        )

    def close(self) -> None:
        with self._state_lock:
            self._runtime = None
            self._closed = True


__all__ = [
    "DEFAULT_HOST",
    "OllamaProvider",
]
