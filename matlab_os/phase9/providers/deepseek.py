"""DeepSeek provider (optional, disabled by default).

Security properties enforced by this module:

* the credential is read from ``os.environ[config.api_key_env]`` **per call**;
* it is placed only into the local ``headers`` mapping handed to the transport
  and is never stored on ``self``, never added to exceptions, audit records or
  ``repr(provider)``;
* a missing credential raises :class:`ProviderUnavailableError` whose message
  names only the environment variable, never its value.

All network access goes through an injected
:class:`~matlab_os.phase9.providers.transport.HttpTransport`, so the whole
provider is testable offline with
:class:`~matlab_os.phase9.providers.transport.MockTransport`.
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping

from ..errors import (
    ProviderError,
    ProviderNotConfiguredError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from .base import (
    ModelProviderBase,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
    coerce_int,
    request_messages,
)
from .transport import (
    HttpTransport,
    UrllibTransport,
    decode_body,
    error_for_status,
    parse_retry_after,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..audit import AuditLog
    from ..config import ProviderConfig

#: Default DeepSeek chat endpoint path.
CHAT_COMPLETIONS_PATH = "/chat/completions"
#: Default model when neither the request nor the configuration names one.
DEFAULT_MODEL = "deepseek-chat"
#: Upper bound applied to any retry delay, as mandated by the contract.
MAX_RETRY_DELAY_SECONDS = 8.0

_SSE_DATA_PREFIX = "data:"
_SSE_DONE = "[DONE]"


def resolve_chat_url(endpoint: str) -> str:
    """Resolve *endpoint* into the full chat-completions URL.

    Accepts a bare origin (``https://api.deepseek.com``), an origin with a
    ``/v1`` suffix, or an endpoint that already ends in
    ``/chat/completions``.
    """
    base = (endpoint or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith(CHAT_COMPLETIONS_PATH):
        return base
    return base + CHAT_COMPLETIONS_PATH


def _as_provider_error(
    exc: BaseException,
    provider_id: str,
) -> ProviderError:
    """Translate any transport exception into a typed Phase 9 error."""
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return ProviderTimeoutError(
            "Provider transport timed out.", provider_id=provider_id
        )
    if isinstance(exc, OSError):
        return ProviderError(
            "Provider transport failed.", provider_id=provider_id, retryable=False
        )
    # Foreign exception text is never echoed: it could carry arbitrary payload.
    return ProviderError(
        "Provider transport failed unexpectedly.",
        provider_id=provider_id,
        retryable=False,
    )


def _parse_sse_line(line: str) -> tuple[str, str] | None:
    """Parse one SSE line into ``("delta", text)``, ``("done", "")`` or None.

    Anything that does not follow the OpenAI-compatible SSE shape is skipped
    without raising: a malformed frame must never abort a stream.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith(":"):
        return None
    if not stripped.lower().startswith(_SSE_DATA_PREFIX):
        # event:, id:, retry: and other SSE fields are irrelevant here.
        return None
    data = stripped[len(_SSE_DATA_PREFIX):].strip()
    if not data:
        return None
    if data == _SSE_DONE:
        return ("done", "")
    try:
        payload = json.loads(data)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return None
    content = delta.get("content")
    if not isinstance(content, str) or not content:
        return None
    return ("delta", content)


class DeepSeekProvider(ModelProviderBase):
    """Remote DeepSeek chat-completions provider."""

    provider_id = "deepseek"
    capabilities = frozenset({"text_generation", "reasoning", "code", "planning"})
    locality = "remote"
    default_model = DEFAULT_MODEL

    def __init__(
        self,
        config: "ProviderConfig",
        transport: HttpTransport | None = None,
        audit: "AuditLog | None" = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(audit=audit, clock=clock)
        if config is None:
            raise ValueError("config is required.")
        self._config = config
        self._transport: HttpTransport = (
            transport if transport is not None else UrllibTransport()
        )
        self._url = resolve_chat_url(config.endpoint)
        if config.model:
            self.default_model = config.model
        self._sleep = time.sleep

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def is_configured(self) -> bool:
        if self.closed:
            return False
        if not self._config.enabled:
            return False
        if not self._url:
            return False
        if self._config.api_key_required and not self._config.api_key_env:
            return False
        return True

    def is_available(self) -> bool:
        if not self.is_configured():
            return False
        if self._config.api_key_required and not self._credential_present():
            return False
        return True

    def _credential_present(self) -> bool:
        env_name = self._config.api_key_env
        return bool(env_name and os.environ.get(env_name))

    def _health_detail(self, configured: bool, available: bool) -> str:
        if not self._config.enabled:
            return "disabled by configuration"
        if not configured:
            return "endpoint or credential source not configured"
        if not available:
            env_name = self._config.api_key_env or "<unset>"
            return f"credential environment variable {env_name} is not set"
        return "ready"

    def _repr_fields(self) -> Mapping[str, Any]:
        return {
            "endpoint": self._url,
            "model": self.default_model,
        }

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #
    def generate(self, request: ProviderRequest) -> ProviderResponse:
        request = self._validate_request(request)
        self._ensure_open()
        credential = self._require_credential()
        payload = self._build_payload(request, stream=False)
        started = self._clock()
        body = self._post_with_retries(credential, payload, stream=False)
        response = self._parse_completion(body, request)
        return replace(response, latency_seconds=self._elapsed(started))

    # ------------------------------------------------------------------ #
    # Streaming
    # ------------------------------------------------------------------ #
    def stream(self, request: ProviderRequest) -> Iterator[str]:
        # Validate eagerly so a disabled/misconfigured provider raises at the
        # call site instead of on first iteration.
        request = self._validate_request(request)
        self._ensure_open()
        credential = self._require_credential()
        payload = self._build_payload(request, stream=True)
        headers = self._headers(credential, stream=True)
        return self._iter_stream(payload, headers)

    def _iter_stream(
        self,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> Iterator[str]:
        deadline = self._clock() + float(self._config.timeout_seconds)
        retries = max(0, int(self._config.max_retries))
        attempt = 0
        emitted = False
        while True:
            try:
                for delta in self._open_stream(payload, headers, deadline):
                    emitted = True
                    yield delta
                return
            except ProviderError as error:
                if not error.retryable or emitted or attempt >= retries:
                    raise
                self._sleep_before_retry(attempt)
                attempt += 1
            except Exception as exc:  # foreign transport exception
                raise _as_provider_error(exc, self.provider_id) from None

    def _open_stream(
        self,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        deadline: float,
    ) -> Iterator[str]:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise ProviderTimeoutError(
                "DeepSeek streaming exceeded the configured timeout budget.",
                provider_id=self.provider_id,
            )
        connect_timeout = max(
            0.001,
            min(float(self._config.connect_timeout_seconds), remaining),
        )
        frames = self._transport.post_stream(
            self._url,
            headers,
            payload,
            timeout=max(0.001, remaining),
            connect_timeout=connect_timeout,
        )
        buffer = ""
        for raw in frames:
            if self._clock() > deadline:
                raise ProviderTimeoutError(
                    "DeepSeek streaming exceeded the configured timeout budget.",
                    provider_id=self.provider_id,
                )
            buffer += decode_body(raw)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                event = _parse_sse_line(line)
                if event is None:
                    continue
                kind, value = event
                if kind == "done":
                    return
                yield value
        # A final line without a trailing newline still counts.
        if buffer.strip():
            event = _parse_sse_line(buffer)
            if event is not None and event[0] == "delta":
                yield event[1]

    # ------------------------------------------------------------------ #
    # Request construction
    # ------------------------------------------------------------------ #
    def _require_credential(self) -> str:
        """Read the credential from the environment; never store it."""
        if not self._config.enabled:
            raise ProviderNotConfiguredError(
                "DeepSeek provider is disabled by configuration.",
                provider_id=self.provider_id,
            )
        env_name = self._config.api_key_env
        if not env_name:
            raise ProviderUnavailableError(
                "DeepSeek provider has no credential environment variable "
                "configured.",
                provider_id=self.provider_id,
            )
        value = os.environ.get(env_name)
        if not value:
            raise ProviderUnavailableError(
                f"Environment variable {env_name} is not set; "
                "the DeepSeek credential is unavailable.",
                provider_id=self.provider_id,
                env_var=env_name,
            )
        return value

    def _build_payload(
        self,
        request: ProviderRequest,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        return {
            "model": request.model or self.default_model,
            "messages": request_messages(request),
            "max_tokens": int(request.max_tokens),
            "temperature": float(request.temperature),
            "stream": bool(stream),
        }

    def _headers(self, credential: str, *, stream: bool) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + credential,
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }

    # ------------------------------------------------------------------ #
    # HTTP exchange
    # ------------------------------------------------------------------ #
    def _post_with_retries(
        self,
        credential: str,
        payload: Mapping[str, Any],
        *,
        stream: bool,
    ) -> bytes | str:
        retries = max(0, int(self._config.max_retries))
        attempt = 0
        while True:
            headers = self._headers(credential, stream=stream)
            try:
                status, response_headers, body = self._transport.post_json(
                    self._url,
                    headers,
                    payload,
                    timeout=float(self._config.timeout_seconds),
                    connect_timeout=float(self._config.connect_timeout_seconds),
                )
            except ProviderError as error:
                failure = error
            except Exception as exc:  # foreign transport exception
                failure = _as_provider_error(exc, self.provider_id)
            else:
                if 200 <= int(status) < 300:
                    return body
                failure = self._map_status(int(status), response_headers)
            if not failure.retryable or attempt >= retries:
                raise failure
            self._sleep_before_retry(attempt)
            attempt += 1

    def _map_status(
        self,
        status: int,
        headers: Mapping[str, Any] | None,
    ) -> ProviderError:
        return error_for_status(
            status,
            provider_id=self.provider_id,
            headers=headers,
            service="DeepSeek",
        )

    def _sleep_before_retry(self, attempt: int) -> None:
        """Exponential backoff capped at 8 seconds."""
        try:
            base = float(self._config.retry_backoff_seconds)
        except (TypeError, ValueError):
            base = 0.0
        delay = base * (2 ** max(0, int(attempt)))
        delay = max(0.0, min(delay, MAX_RETRY_DELAY_SECONDS))
        if delay > 0:
            self._sleep(delay)

    # ------------------------------------------------------------------ #
    # Response parsing
    # ------------------------------------------------------------------ #
    def _parse_completion(
        self,
        body: bytes | str,
        request: ProviderRequest,
    ) -> ProviderResponse:
        data = self._decode_json(body)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderResponseError(
                "DeepSeek response did not contain a non-empty 'choices' array.",
                provider_id=self.provider_id,
            )
        first = choices[0]
        if not isinstance(first, dict):
            raise ProviderResponseError(
                "DeepSeek response choice was not an object.",
                provider_id=self.provider_id,
            )
        message = first.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderResponseError(
                "DeepSeek response did not contain choices[0].message.content.",
                provider_id=self.provider_id,
            )

        usage_data = data.get("usage")
        if not isinstance(usage_data, dict):
            usage_data = {}
        prompt_tokens = coerce_int(usage_data.get("prompt_tokens"))
        completion_tokens = coerce_int(usage_data.get("completion_tokens"))
        default_total = prompt_tokens + completion_tokens
        total_tokens = coerce_int(usage_data.get("total_tokens"), default=default_total)
        if total_tokens < 0:
            total_tokens = default_total

        response_model = data.get("model")
        if not isinstance(response_model, str) or not response_model:
            response_model = request.model or self.default_model

        finish_reason = first.get("finish_reason")
        if not isinstance(finish_reason, str) or not finish_reason:
            finish_reason = "stop"

        return ProviderResponse(
            provider_id=self.provider_id,
            model=response_model,
            text=content,
            usage=ProviderUsage(
                prompt_tokens=max(0, prompt_tokens),
                completion_tokens=max(0, completion_tokens),
                total_tokens=max(0, total_tokens),
            ),
            latency_seconds=0.0,
            finish_reason=finish_reason,
            streamed=False,
        )

    def _decode_json(self, body: bytes | str) -> dict[str, Any]:
        text = decode_body(body)
        try:
            data = json.loads(text)
        except ValueError:
            # Never chain the decoding exception: its message can quote bytes.
            raise ProviderResponseError(
                "DeepSeek returned a malformed JSON body.",
                provider_id=self.provider_id,
            ) from None
        if not isinstance(data, dict):
            raise ProviderResponseError(
                "DeepSeek returned a JSON body that is not an object.",
                provider_id=self.provider_id,
            )
        return data


__all__ = [
    "CHAT_COMPLETIONS_PATH",
    "DEFAULT_MODEL",
    "DeepSeekProvider",
    "MAX_RETRY_DELAY_SECONDS",
    "parse_retry_after",
    "resolve_chat_url",
]
