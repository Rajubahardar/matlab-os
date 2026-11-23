"""Deterministic, offline provider used for tests and fallbacks.

``MockProvider`` never touches the network, never reads the environment and
never sleeps: the same request against the same provider state always produces
the same response.  That makes it the reference implementation for the
registry's deterministic ordering and fallback path.
"""

from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING, Any, Callable, Iterator

from ..errors import (
    Phase9Error,
    ProviderError,
    ProviderNotConfiguredError,
)
from .base import (
    ModelProviderBase,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..audit import AuditLog
    from ..config import ProviderConfig

#: Default model label reported by the offline provider.
DEFAULT_MODEL = "mock-model"
#: Deterministic chunk size used by :meth:`MockProvider.stream`.
STREAM_CHUNK_SIZE = 16


class MockProvider(ModelProviderBase):
    """Scripted provider that is fully deterministic and offline."""

    provider_id = "mock"
    capabilities = frozenset({"text_generation"})
    locality = "local"
    default_model = DEFAULT_MODEL

    def __init__(
        self,
        config: "ProviderConfig | None" = None,
        *,
        provider_id: str | None = None,
        responses: Any = (),
        audit: "AuditLog | None" = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(audit=audit, clock=clock)
        self._config = config
        if config is not None:
            configured_id = getattr(config, "provider_id", "")
            if configured_id:
                self.provider_id = str(configured_id)
            configured_capabilities = tuple(getattr(config, "capabilities", ()) or ())
            if configured_capabilities:
                self.capabilities = frozenset(configured_capabilities)
            configured_locality = getattr(config, "locality", "") or ""
            if configured_locality:
                self.locality = configured_locality
            configured_model = getattr(config, "model", "") or ""
            if configured_model:
                self.default_model = configured_model
        if provider_id:
            self.provider_id = str(provider_id)
        self._responses: deque[str] = deque(str(item) for item in (responses or ()))
        self._errors: deque[BaseException] = deque()
        self._calls = 0

    # ------------------------------------------------------------------ #
    # Scripting helpers
    # ------------------------------------------------------------------ #
    def script(self, text: str) -> None:
        """Queue one scripted response text."""
        with self._state_lock:
            self._responses.append(str(text))

    def script_error(self, error: BaseException) -> None:
        """Queue one simulated failure (raised by the next call)."""
        if not isinstance(error, BaseException):
            raise TypeError("error must be a BaseException instance.")
        with self._state_lock:
            self._errors.append(error)

    def call_count(self) -> int:
        with self._state_lock:
            return self._calls

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def is_configured(self) -> bool:
        if self.closed:
            return False
        if self._config is None:
            return True
        return bool(self._config.enabled)

    def is_available(self) -> bool:
        return self.is_configured()

    # ------------------------------------------------------------------ #
    # Provider API
    # ------------------------------------------------------------------ #
    def generate(self, request: ProviderRequest) -> ProviderResponse:
        request = self._validate_request(request)
        self._ensure_open()
        if not self.is_configured():
            raise ProviderNotConfiguredError(
                f"Provider {self.provider_id!r} is disabled by configuration.",
                provider_id=self.provider_id,
            )

        error: BaseException | None = None
        scripted: str | None = None
        with self._state_lock:
            self._calls += 1
            if self._errors:
                error = self._errors.popleft()
            if self._responses:
                scripted = self._responses.popleft()

        if error is not None:
            if isinstance(error, Phase9Error):
                raise error
            # Never echo foreign exception text: it could carry a payload.
            raise ProviderError(
                f"Provider {self.provider_id!r} simulated a failure.",
                provider_id=self.provider_id,
                retryable=False,
            )

        started = self._clock()
        text = scripted if scripted is not None else self._default_text(request)
        return self._build_response(
            request,
            text,
            self._elapsed(started),
            scripted=scripted is not None,
        )

    def stream(self, request: ProviderRequest) -> Iterator[str]:
        response = self.generate(request)
        return self._iter_chunks(response.text)

    @staticmethod
    def _iter_chunks(text: str, size: int = STREAM_CHUNK_SIZE) -> Iterator[str]:
        for index in range(0, len(text), size):
            yield text[index:index + size]

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _default_text(request: ProviderRequest) -> str:
        parts = [f"mock[{request.model or DEFAULT_MODEL}]"]
        if request.system:
            parts.append(request.system)
        parts.append(request.prompt)
        return " | ".join(parts)

    def _build_response(
        self,
        request: ProviderRequest,
        text: str,
        latency: float,
        *,
        scripted: bool,
    ) -> ProviderResponse:
        prompt_words = f"{request.system} {request.prompt}".split()
        completion_words = text.split()
        prompt_tokens = len(prompt_words)
        completion_tokens = len(completion_words)
        return ProviderResponse(
            provider_id=self.provider_id,
            model=request.model or self.default_model,
            text=text,
            usage=ProviderUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            latency_seconds=max(0.0, latency),
            finish_reason="stop",
            streamed=False,
            metadata={"scripted": scripted, "offline": True},
        )


__all__ = [
    "DEFAULT_MODEL",
    "MockProvider",
    "STREAM_CHUNK_SIZE",
]
