"""HTTP transport abstraction for Phase 9 providers.

Two implementations exist:

* :class:`UrllibTransport` — the production transport, implemented with
  ``urllib.request`` only (stdlib, no third-party HTTP client).  It sets a
  User-Agent, uses ``connect_timeout`` as the socket connect timeout, caps a
  response body at 8 MiB, and never retains request headers (and therefore
  never retains the ``Authorization`` credential).
* :class:`MockTransport` — a scripted, fully offline transport used by tests
  and by any provider constructor that accepts dependency injection.

The transport is deliberately dumb about *semantics*: it returns status,
headers and bytes.  Provider modules translate those into typed Phase 9
errors with :func:`error_for_status`.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Iterable, Iterator, Mapping, Protocol, runtime_checkable

from ..errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

#: ``(status, headers, body)`` returned by the non-streaming transport calls.
StatusAndBody = tuple[int, Mapping[str, str], bytes]

#: Hard cap on a single non-streaming response body (8 MiB).
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

_TRANSPORT_ID = "http_transport"


@runtime_checkable
class HttpTransport(Protocol):
    """The minimal HTTP surface every provider is allowed to use."""

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        *,
        timeout: float,
        connect_timeout: float,
    ) -> StatusAndBody:
        """POST a JSON payload and return status, headers and body bytes."""

    def post_stream(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        *,
        timeout: float,
        connect_timeout: float,
    ) -> Iterator[bytes]:
        """POST a JSON payload and iterate the raw response chunks."""

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        timeout: float,
        connect_timeout: float,
    ) -> StatusAndBody:
        """GET a URL and return status, headers and body bytes."""


def decode_body(body: bytes | bytearray | str) -> str:
    """Decode a response body leniently; invalid UTF-8 is replaced, never raised."""
    if isinstance(body, str):
        return body
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8", errors="replace")
    return str(body)


def _header_value(headers: Mapping[str, Any] | None, name: str) -> Any | None:
    """Case-insensitive header lookup; returns None when absent."""
    if not headers:
        return None
    target = name.lower()
    try:
        items = headers.items()
    except AttributeError:
        return None
    for key, value in items:
        if str(key).lower() == target:
            return value
    return None


def parse_retry_after(headers: Mapping[str, Any] | None) -> float | None:
    """Parse a numeric ``Retry-After`` header value.

    HTTP-date values are deliberately ignored (``None``) because the contract
    only promises numeric parsing; no clock arithmetic is invented.
    """
    raw = _header_value(headers, "retry-after")
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return seconds


def error_for_status(
    status: int,
    *,
    provider_id: str | None = None,
    headers: Mapping[str, Any] | None = None,
    service: str = "Provider",
) -> ProviderError:
    """Translate an HTTP status into the typed Phase 9 provider error.

    The body is intentionally *not* included: provider payloads must never be
    echoed into exception messages.
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        code = 0

    if code in (401, 403):
        return ProviderAuthError(
            f"{service} rejected the credential (HTTP {code}).",
            provider_id=provider_id,
            status=code,
        )
    if code == 429:
        return ProviderRateLimitError(
            f"{service} rate limited the request (HTTP 429).",
            provider_id=provider_id,
            retry_after=parse_retry_after(headers),
            status=code,
        )
    if code >= 500:
        return ProviderError(
            f"{service} reported a service error (HTTP {code}).",
            provider_id=provider_id,
            retryable=True,
            status=code,
        )
    if 400 <= code < 500:
        return ProviderError(
            f"{service} rejected the request (HTTP {code}).",
            provider_id=provider_id,
            retryable=False,
            status=code,
        )
    return ProviderResponseError(
        f"{service} returned an unexpected HTTP status ({code}).",
        provider_id=provider_id,
        status=code,
    )


class UrllibTransport:
    """stdlib ``urllib.request`` transport.

    Guarantees:
    * a ``User-Agent`` is always sent;
    * ``connect_timeout`` bounds connection establishment (socket timeout);
    * a response body larger than :data:`MAX_RESPONSE_BYTES` raises
      :class:`ProviderResponseError` instead of being buffered;
    * request headers (including credentials) are local variables only — they
      are never logged or stored on the instance.
    """

    USER_AGENT = "Phase9-LocalAIOS/9.0 (staging; stdlib-urllib)"

    _JSON_HEADERS: Mapping[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    _STREAM_HEADERS: Mapping[str, str] = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    _READ_CHUNK = 65536

    def __init__(
        self,
        *,
        user_agent: str = USER_AGENT,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        if not isinstance(user_agent, str) or not user_agent:
            raise ValueError("user_agent must be a non-empty string.")
        if int(max_response_bytes) < 1:
            raise ValueError("max_response_bytes must be positive.")
        self._user_agent = user_agent
        self._max_response_bytes = int(max_response_bytes)

    # ------------------------------------------------------------------ #
    # Public transport API
    # ------------------------------------------------------------------ #
    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        *,
        timeout: float,
        connect_timeout: float,
    ) -> StatusAndBody:
        data = self._encode_payload(payload)
        merged = self._merge_headers(headers, self._JSON_HEADERS)
        return self._send(
            "POST", url, data, merged,
            timeout=timeout, connect_timeout=connect_timeout,
        )

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        timeout: float,
        connect_timeout: float,
    ) -> StatusAndBody:
        merged = self._merge_headers(headers, {"Accept": "application/json"})
        return self._send(
            "GET", url, None, merged,
            timeout=timeout, connect_timeout=connect_timeout,
        )

    def post_stream(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        *,
        timeout: float,
        connect_timeout: float,
    ) -> Iterator[bytes]:
        data = self._encode_payload(payload)
        merged = self._merge_headers(headers, self._STREAM_HEADERS)
        request = self._build_request("POST", url, data, merged)

        try:
            response = self._open(request, connect_timeout)
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            response_headers = self._headers_of(exc)
            try:
                exc.close()
            except Exception:  # pragma: no cover - defensive close
                pass
            raise error_for_status(
                status,
                provider_id=_TRANSPORT_ID,
                headers=response_headers,
                service="HTTP transport",
            ) from None
        except Exception as exc:
            raise self._translate_error(exc) from None

        deadline = time.monotonic() + max(0.001, float(timeout))
        return self._iter_response(response, deadline)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _encode_payload(payload: Mapping[str, Any]) -> bytes:
        try:
            rendered = json.dumps(
                payload, separators=(",", ":"), ensure_ascii=False
            )
        except (TypeError, ValueError):
            raise ProviderResponseError(
                "Provider request payload is not JSON serialisable.",
                provider_id=_TRANSPORT_ID,
            ) from None
        return rendered.encode("utf-8")

    @staticmethod
    def _merge_headers(
        headers: Mapping[str, str] | None,
        defaults: Mapping[str, str],
    ) -> dict[str, str]:
        merged = dict(defaults)
        if headers:
            merged.update({str(key): str(value) for key, value in headers.items()})
        return merged

    def _build_request(
        self,
        method: str,
        url: str,
        data: bytes | None,
        headers: Mapping[str, str],
    ) -> urllib.request.Request:
        merged = {str(key): str(value) for key, value in headers.items()}
        # Always send our own User-Agent; a caller cannot suppress it.
        merged["User-Agent"] = self._user_agent
        return urllib.request.Request(url, data=data, headers=merged, method=method)

    def _open(self, request: urllib.request.Request, connect_timeout: float):
        effective = max(0.001, float(connect_timeout))
        return urllib.request.urlopen(request, timeout=effective)

    def _send(
        self,
        method: str,
        url: str,
        data: bytes | None,
        headers: Mapping[str, str],
        *,
        timeout: float,
        connect_timeout: float,
    ) -> StatusAndBody:
        request = self._build_request(method, url, data, headers)
        try:
            response = self._open(request, connect_timeout)
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            response_headers = self._headers_of(exc)
            try:
                body = self._read_limited(exc)
            except ProviderError:
                raise
            except Exception:
                body = b""
            finally:
                try:
                    exc.close()
                except Exception:  # pragma: no cover - defensive close
                    pass
            return status, response_headers, body
        except Exception as exc:
            raise self._translate_error(exc) from None

        try:
            self._apply_read_timeout(response, timeout)
            status = self._status_of(response)
            response_headers = self._headers_of(response)
            body = self._read_limited(response)
            return status, response_headers, body
        except ProviderError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from None
        finally:
            try:
                response.close()
            except Exception:  # pragma: no cover - defensive close
                pass

    def _iter_response(self, response: Any, deadline: float) -> Iterator[bytes]:
        try:
            remaining = deadline - time.monotonic()
            self._apply_read_timeout(response, max(0.001, remaining))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderTimeoutError(
                        "HTTP stream exceeded its timeout budget.",
                        provider_id=_TRANSPORT_ID,
                    )
                chunk = response.read(self._READ_CHUNK)
                if not chunk:
                    return
                yield bytes(chunk)
        except ProviderError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from None
        finally:
            try:
                response.close()
            except Exception:  # pragma: no cover - defensive close
                pass

    @staticmethod
    def _apply_read_timeout(response: Any, timeout: float) -> None:
        """Extend the socket read timeout to the overall request budget.

        ``urlopen`` applies ``connect_timeout`` to the socket.  Once connected,
        the overall ``timeout`` should govern reads, so the socket timeout is
        raised.  Socket introspection is implementation detail; failure is
        harmless (reads then keep the conservative connect timeout).
        """
        if timeout <= 0:
            return
        stream = getattr(response, "fp", None)
        for _ in range(4):
            if stream is None:
                return
            sock = getattr(stream, "_sock", None)
            if sock is not None:
                try:
                    sock.settimeout(timeout)
                except (OSError, AttributeError):
                    pass
                return
            stream = getattr(stream, "raw", None) or getattr(stream, "fp", None)

    @staticmethod
    def _status_of(response: Any) -> int:
        status = getattr(response, "status", None)
        if status is None:
            getcode = getattr(response, "getcode", None)
            status = getcode() if callable(getcode) else 0
        try:
            return int(status or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _headers_of(message: Any) -> dict[str, str]:
        headers = getattr(message, "headers", None)
        if headers is None:
            return {}
        try:
            return {str(key): str(value) for key, value in headers.items()}
        except (AttributeError, TypeError):  # pragma: no cover - defensive
            return {}

    def _read_limited(self, handle: Any) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = handle.read(self._READ_CHUNK)
            if not chunk:
                break
            if not isinstance(chunk, (bytes, bytearray)):
                chunk = bytes(chunk)
            total += len(chunk)
            if total > self._max_response_bytes:
                raise ProviderResponseError(
                    "Response body exceeded the transport limit of "
                    f"{self._max_response_bytes} bytes.",
                    provider_id=_TRANSPORT_ID,
                    limit=self._max_response_bytes,
                )
            chunks.append(bytes(chunk))
        return b"".join(chunks)

    @staticmethod
    def _translate_error(exc: BaseException) -> ProviderError:
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return ProviderTimeoutError(
                "HTTP transport timed out.", provider_id=_TRANSPORT_ID
            )
        if isinstance(exc, urllib.error.URLError):
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                return ProviderTimeoutError(
                    "HTTP transport timed out while connecting.",
                    provider_id=_TRANSPORT_ID,
                )
            return ProviderUnavailableError(
                "HTTP transport could not reach the provider endpoint.",
                provider_id=_TRANSPORT_ID,
            )
        if isinstance(exc, ValueError):
            return ProviderError(
                "HTTP transport received an invalid URL.",
                provider_id=_TRANSPORT_ID,
            )
        if isinstance(exc, OSError):
            return ProviderError(
                "HTTP transport failed.", provider_id=_TRANSPORT_ID
            )
        return ProviderError(
            "HTTP transport failed unexpectedly.", provider_id=_TRANSPORT_ID
        )


class MockTransport:
    """Scripted transport used by tests and offline implementations.

    Queues are consumed in order and shared between :meth:`post_json` and
    :meth:`get`; streaming has its own queue.  Every call is recorded in
    :attr:`calls` so a test can assert request shape, headers and timeouts
    without any network access.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._responses: deque[StatusAndBody] = deque()
        self._errors: deque[BaseException] = deque()
        self._streams: deque[list[bytes]] = deque()
        #: Recorded non-streaming/streaming calls, oldest first.
        self.calls: list[dict[str, Any]] = []
        #: Raw frame lists observed for each :meth:`post_stream` call.
        self.stream_frames: list[list[bytes]] = []

    # ------------------------------------------------------------------ #
    # Scripting helpers
    # ------------------------------------------------------------------ #
    def queue_response(
        self,
        status: int,
        headers: Mapping[str, str] | None = None,
        body: bytes | bytearray | str = b"",
    ) -> None:
        """Queue a ``(status, headers, body)`` reply for the next call."""
        if isinstance(body, (bytes, bytearray)):
            encoded = bytes(body)
        else:
            encoded = str(body).encode("utf-8")
        safe_headers = {str(key): str(value) for key, value in (headers or {}).items()}
        with self._lock:
            self._responses.append((int(status), safe_headers, encoded))

    def queue_error(self, exception: BaseException) -> None:
        """Queue an exception raised by the next call instead of a reply."""
        if not isinstance(exception, BaseException):
            raise TypeError("exception must be a BaseException instance.")
        with self._lock:
            self._errors.append(exception)

    def queue_stream(self, chunks: Iterable[bytes | str]) -> None:
        """Queue the frames yielded by the next :meth:`post_stream` call."""
        frames: list[bytes] = []
        for chunk in chunks:
            if isinstance(chunk, (bytes, bytearray)):
                frames.append(bytes(chunk))
            else:
                frames.append(str(chunk).encode("utf-8"))
        with self._lock:
            self._streams.append(frames)

    # ------------------------------------------------------------------ #
    # Introspection helpers
    # ------------------------------------------------------------------ #
    def pending_responses(self) -> int:
        with self._lock:
            return len(self._responses)

    def pending_errors(self) -> int:
        with self._lock:
            return len(self._errors)

    def pending_streams(self) -> int:
        with self._lock:
            return len(self._streams)

    def reset(self) -> None:
        with self._lock:
            self._responses.clear()
            self._errors.clear()
            self._streams.clear()
            self.calls.clear()
            self.stream_frames.clear()

    # ------------------------------------------------------------------ #
    # Transport API
    # ------------------------------------------------------------------ #
    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        *,
        timeout: float,
        connect_timeout: float,
    ) -> StatusAndBody:
        self._record("POST", url, headers, payload, timeout, connect_timeout)
        return self._take_response()

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        timeout: float,
        connect_timeout: float,
    ) -> StatusAndBody:
        self._record("GET", url, headers, {}, timeout, connect_timeout)
        return self._take_response()

    def _take_response(self) -> StatusAndBody:
        with self._lock:
            error = self._errors.popleft() if self._errors else None
            if error is not None:
                raise error
            if not self._responses:
                raise ProviderUnavailableError(
                    "MockTransport has no scripted response for this call.",
                    provider_id="mock_transport",
                )
            return self._responses.popleft()

    def post_stream(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        *,
        timeout: float,
        connect_timeout: float,
    ) -> Iterator[bytes]:
        self._record("POST", url, headers, payload, timeout, connect_timeout)
        with self._lock:
            error = self._errors.popleft() if self._errors else None
            if error is not None:
                raise error
            if not self._streams:
                raise ProviderUnavailableError(
                    "MockTransport has no scripted stream for this call.",
                    provider_id="mock_transport",
                )
            frames = self._streams.popleft()
            self.stream_frames.append(list(frames))
        return iter(list(frames))

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _record(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
        connect_timeout: float,
    ) -> None:
        call = {
            "method": method,
            "url": url,
            "headers": {str(key): str(value) for key, value in headers.items()},
            "payload": dict(payload),
            "timeout": float(timeout),
            "connect_timeout": float(connect_timeout),
        }
        with self._lock:
            self.calls.append(call)


__all__ = [
    "HttpTransport",
    "MAX_RESPONSE_BYTES",
    "MockTransport",
    "StatusAndBody",
    "UrllibTransport",
    "decode_body",
    "error_for_status",
    "parse_retry_after",
]
