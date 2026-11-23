"""Provider contract for the Phase 9 model layer.

The abstraction is deliberately small:

* :class:`ProviderRequest`, :class:`ProviderUsage` and
  :class:`ProviderResponse` are immutable value objects shared by every
  provider implementation.
* :class:`ModelProviderBase` owns the lifecycle every provider must obey and
  the two cross-cutting guarantees of the layer: failures are always typed
  Phase 9 errors, and no representation of a provider can expose a credential.

Secrets never live on an instance.  A provider that needs a credential reads it
from the process environment at call time (see
:class:`matlab_os.phase9.providers.deepseek.DeepSeekProvider`).
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping

from ..errors import ProviderUnavailableError
from ..security.redaction import redact

#: A monotonic-ish time source (``time.time`` by default); injectable so tests
#: never depend on wall-clock races.
Clock = Callable[[], float]


@dataclass(frozen=True)
class ProviderRequest:
    """One unit of work sent to a model provider."""

    prompt: str
    system: str = ""
    model: str = ""
    max_tokens: int = 1024
    temperature: float = 0.2
    stream: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderUsage:
    """Token accounting reported by a provider."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class ProviderResponse:
    """A completed (non-streamed) provider answer."""

    provider_id: str
    model: str
    text: str
    usage: ProviderUsage = ProviderUsage()
    latency_seconds: float = 0.0
    finish_reason: str = "stop"
    streamed: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


def coerce_int(value: Any, default: int = 0) -> int:
    """Best-effort integer coercion used for provider usage counters.

    Provider payloads are untrusted, so a malformed counter must never raise:
    it degrades to *default* instead.
    """
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def request_messages(request: ProviderRequest) -> list[dict[str, str]]:
    """Render *request* as chat messages (system message only when present)."""
    messages: list[dict[str, str]] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    messages.append({"role": "user", "content": request.prompt})
    return messages


class ModelProviderBase(ABC):
    """Common lifecycle and guarantees for every Phase 9 provider.

    Subclasses set :attr:`provider_id`, :attr:`capabilities` and
    :attr:`locality` as class attributes and implement :meth:`generate` and
    :meth:`stream`.  Implementations must route all remote I/O through an
    injected transport so that the object graph stays testable offline.
    """

    provider_id: str = "provider"
    capabilities: frozenset[str] = frozenset()
    locality: str = "local"
    #: Model used when a request does not name one explicitly.
    default_model: str = ""

    def __init__(
        self,
        *,
        audit: Any | None = None,
        clock: Clock = time.time,
    ) -> None:
        if self.locality not in {"local", "remote"}:
            raise ValueError(
                f"locality must be 'local' or 'remote', got {self.locality!r}"
            )
        self._audit = audit
        self._clock: Clock = clock if callable(clock) else time.time
        self._closed = False
        self._state_lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #
    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed

    @property
    def audit(self) -> Any | None:
        return self._audit

    @property
    def clock(self) -> Clock:
        return self._clock

    def _ensure_open(self) -> None:
        """Raise when the provider was closed and must not be used again."""
        if self.closed:
            raise ProviderUnavailableError(
                f"Provider {self.provider_id!r} is closed.",
                provider_id=self.provider_id,
            )

    def _elapsed(self, started: float) -> float:
        """Seconds elapsed since *started*, never negative."""
        return max(0.0, float(self._clock() - started))

    @staticmethod
    def _validate_request(request: Any) -> ProviderRequest:
        """Reject a wrong request type as a programmer error (``TypeError``)."""
        if not isinstance(request, ProviderRequest):
            raise TypeError("request must be a ProviderRequest instance.")
        return request

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def is_configured(self) -> bool:
        """Return True when the provider holds a complete configuration.

        The default implementation only checks the lifecycle.  Remote
        providers additionally require an endpoint and credential source.
        """
        return not self.closed

    def is_available(self) -> bool:
        """Return True when the provider can serve a request right now."""
        return self.is_configured()

    def _health_detail(self, configured: bool, available: bool) -> str:
        if not configured:
            return "not configured"
        return "ready" if available else "unavailable"

    def health(self) -> dict[str, Any]:
        """Return a secret-free status snapshot; never raises."""
        configured = False
        available = False
        try:
            configured = bool(self.is_configured())
        except Exception:
            configured = False
        try:
            available = bool(self.is_available()) if configured else False
        except Exception:
            available = False
        try:
            detail = self._health_detail(configured, available)
        except Exception:
            detail = "state unavailable"
        payload = {
            "provider_id": self.provider_id,
            "configured": configured,
            "available": available,
            "detail": detail,
        }
        safe = redact(payload)
        return safe if isinstance(safe, dict) else payload

    # ------------------------------------------------------------------ #
    # Presentation
    # ------------------------------------------------------------------ #
    def _repr_fields(self) -> Mapping[str, Any]:
        """Extra non-secret fields for :meth:`__repr__` (override carefully)."""
        return {}

    def __repr__(self) -> str:
        """Redacting representation: a credential can never appear here.

        Only non-secret state is rendered (identity, locality, configured flag
        and capability names).  The result is passed through the shared
        redactor as a last line of defence.
        """
        try:
            configured = bool(self.is_configured())
        except Exception:
            configured = False
        parts = [
            f"provider_id={self.provider_id!r}",
            f"locality={self.locality!r}",
            f"configured={configured}",
            f"capabilities={sorted(self.capabilities)!r}",
        ]
        try:
            extra = self._repr_fields()
        except Exception:
            extra = {}
        for key in sorted(extra):
            parts.append(f"{key}={extra[key]!r}")
        rendered = f"<{type(self).__name__} " + " ".join(parts) + ">"
        return str(redact(rendered))

    # ------------------------------------------------------------------ #
    # Provider API
    # ------------------------------------------------------------------ #
    @abstractmethod
    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Return one complete response. Never raises a non-Phase-9 error."""

    @abstractmethod
    def stream(self, request: ProviderRequest) -> Iterator[str]:
        """Yield text deltas. Never raises a non-Phase-9 error."""

    def close(self) -> None:
        """Release provider resources; idempotent."""
        with self._state_lock:
            self._closed = True


__all__ = [
    "Clock",
    "ModelProviderBase",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderUsage",
    "coerce_int",
    "request_messages",
]
