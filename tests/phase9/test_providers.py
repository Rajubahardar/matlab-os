"""Tests for the Phase 9 provider layer.

Every test is offline: DeepSeek traffic goes through
:class:`matlab_os.phase9.providers.transport.MockTransport`, the local
provider wraps a fake runtime, and the production ``UrllibTransport`` is
exercised through a monkeypatched ``urllib.request.urlopen``.

No test uses a real credential; a dummy value is injected through
``monkeypatch`` and asserted absent from every message, representation and
audit record.
"""

from __future__ import annotations

import email.message
import io
import json
import socket
import urllib.error
import urllib.request

import pytest

from matlab_os.phase9.audit import AuditLog
from matlab_os.phase9.config import Phase9Config, ProviderConfig
from matlab_os.phase9.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from matlab_os.phase9.providers.base import (
    ModelProviderBase,
    ProviderRequest,
    ProviderUsage,
)
from matlab_os.phase9.providers.deepseek import DeepSeekProvider
from matlab_os.phase9.providers.mock import MockProvider
from matlab_os.phase9.providers.ollama import OllamaProvider
from matlab_os.phase9.providers.registry import ProviderRegistry
from matlab_os.phase9.providers.transport import (
    MockTransport,
    UrllibTransport,
)

ENV_NAME = "PHASE9_TEST_DEEPSEEK_API_KEY"
DUMMY_SECRET = "sk-phase9-dummy-0123456789abcdef"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class FakeClock:
    """Deterministic clock: every call returns ``now`` then advances."""

    def __init__(self, start: float = 1000.0, step: float = 0.25) -> None:
        self._now = float(start)
        self._step = float(step)

    def __call__(self) -> float:
        value = self._now
        self._now += self._step
        return value


@pytest.fixture
def api_key(monkeypatch):
    """Install a dummy DeepSeek credential for the duration of a test."""
    monkeypatch.setenv(ENV_NAME, DUMMY_SECRET)
    return DUMMY_SECRET


def deepseek_config(**overrides) -> ProviderConfig:
    data = {
        "provider_id": "deepseek",
        "kind": "deepseek",
        "enabled": True,
        "endpoint": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "timeout_seconds": 5.0,
        "connect_timeout_seconds": 1.0,
        "max_retries": 0,
        "retry_backoff_seconds": 0.0,
        "stream": False,
        "api_key_env": ENV_NAME,
        "api_key_required": True,
        "tls_verify": True,
        "locality": "remote",
        "max_tokens": 256,
        "temperature": 0.3,
        "capabilities": ("text_generation", "reasoning", "code", "planning"),
    }
    data.update(overrides)
    config = ProviderConfig(**data)
    config.validate()
    return config


def ollama_config(**overrides) -> ProviderConfig:
    data = {
        "provider_id": "ollama_local",
        "kind": "ollama",
        "enabled": True,
        "endpoint": "http://127.0.0.1:11435",
        "model": "llama3",
        "timeout_seconds": 10.0,
        "connect_timeout_seconds": 2.0,
        "max_retries": 0,
        "retry_backoff_seconds": 0.0,
        "stream": False,
        "api_key_env": None,
        "api_key_required": False,
        "tls_verify": False,
        "locality": "local",
        "max_tokens": 128,
        "temperature": 0.1,
        "capabilities": ("text_generation", "local_inference"),
    }
    data.update(overrides)
    config = ProviderConfig(**data)
    config.validate()
    return config


def mock_config(provider_id: str, **overrides) -> ProviderConfig:
    data = {
        "provider_id": provider_id,
        "kind": "mock",
        "enabled": True,
        "endpoint": "",
        "model": "",
        "timeout_seconds": 10.0,
        "connect_timeout_seconds": 2.0,
        "max_retries": 0,
        "retry_backoff_seconds": 0.0,
        "stream": False,
        "api_key_env": None,
        "api_key_required": False,
        "tls_verify": False,
        "locality": "local",
        "capabilities": ("text_generation",),
    }
    data.update(overrides)
    config = ProviderConfig(**data)
    config.validate()
    return config


def make_deepseek(transport: MockTransport, *, clock=None, **overrides) -> DeepSeekProvider:
    return DeepSeekProvider(
        deepseek_config(**overrides),
        transport=transport,
        clock=clock or FakeClock(),
    )


def completion_body(
    content: str = "Answer",
    *,
    model: str = "deepseek-chat",
    prompt_tokens: int = 11,
    completion_tokens: int = 5,
    finish_reason: str = "stop",
) -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
    ).encode("utf-8")


def sse_delta(content: str) -> bytes:
    payload = {"choices": [{"index": 0, "delta": {"content": content}}]}
    return ("data: " + json.dumps(payload) + "\n\n").encode("utf-8")


def header_value(headers: dict, name: str) -> str | None:
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return value
    return None


class _FakeRuntime:
    """Fake ``OllamaRuntimeAdapter`` for provider conversion tests."""

    def __init__(self, *, available=True, result=None, error=None) -> None:
        self._available = available
        self._result = result
        self._error = error
        self.calls: list[dict] = []

    def is_available(self) -> bool:
        if isinstance(self._available, BaseException):
            raise self._available
        return bool(self._available)

    def generate(self, name, prompt, options=None):
        self.calls.append({"name": name, "prompt": prompt, "options": options})
        if self._error is not None:
            raise self._error
        return dict(self._result or {})


class _FakeHeaders:
    def __init__(self, items) -> None:
        self._items = list(items)

    def items(self):
        return list(self._items)


class _FakeResponse:
    """Minimal ``urlopen`` result used to exercise ``UrllibTransport``."""

    def __init__(self, chunks, *, status=200, headers=None) -> None:
        self._chunks = list(chunks)
        self.status = status
        self.headers = _FakeHeaders(headers or [])
        self.fp = None
        self.closed = False

    def read(self, size=-1) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


# --------------------------------------------------------------------------- #
# DeepSeek: configuration, credential and request construction
# --------------------------------------------------------------------------- #
def test_disabled_deepseek_raises_not_configured():
    transport = MockTransport()
    provider = make_deepseek(transport, enabled=False)

    with pytest.raises(ProviderNotConfiguredError):
        provider.generate(ProviderRequest(prompt="hello"))
    with pytest.raises(ProviderNotConfiguredError):
        provider.stream(ProviderRequest(prompt="hello"))
    assert transport.calls == []


def test_missing_credential_names_env_and_never_leaks_value(monkeypatch):
    # Install a dummy secret first, then remove it: the value must never
    # appear in the resulting diagnostic even though the test knew it.
    monkeypatch.setenv(ENV_NAME, DUMMY_SECRET)
    transport = MockTransport()
    provider = make_deepseek(transport)
    monkeypatch.delenv(ENV_NAME, raising=False)

    with pytest.raises(ProviderUnavailableError) as info:
        provider.generate(ProviderRequest(prompt="hello"))
    message = str(info.value)
    assert ENV_NAME in message
    assert DUMMY_SECRET not in message
    assert DUMMY_SECRET not in repr(info.value.describe())

    with pytest.raises(ProviderUnavailableError):
        provider.stream(ProviderRequest(prompt="hello"))
    assert transport.calls == []


def test_generate_parses_content_usage_and_latency(api_key):
    transport = MockTransport()
    transport.queue_response(
        200,
        {"Content-Type": "application/json"},
        completion_body("Answer", prompt_tokens=11, completion_tokens=5),
    )
    provider = make_deepseek(transport, clock=FakeClock(start=100.0, step=0.5))

    response = provider.generate(ProviderRequest(prompt="question"))

    assert response.provider_id == "deepseek"
    assert response.model == "deepseek-chat"
    assert response.text == "Answer"
    assert response.usage == ProviderUsage(
        prompt_tokens=11, completion_tokens=5, total_tokens=16
    )
    assert response.finish_reason == "stop"
    assert response.streamed is False
    assert response.latency_seconds == pytest.approx(0.5)


def test_request_body_shape(api_key):
    transport = MockTransport()
    transport.queue_response(200, {}, completion_body("ok"))
    provider = make_deepseek(transport)

    provider.generate(
        ProviderRequest(
            prompt="ping",
            system="be terse",
            model="deepseek-reasoner",
            max_tokens=64,
            temperature=0.9,
        )
    )

    call = transport.calls[0]
    payload = call["payload"]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.deepseek.com/chat/completions"
    assert payload["model"] == "deepseek-reasoner"
    assert payload["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "ping"},
    ]
    assert payload["max_tokens"] == 64
    assert payload["temperature"] == pytest.approx(0.9)
    assert payload["stream"] is False
    assert call["timeout"] == pytest.approx(5.0)
    assert call["connect_timeout"] == pytest.approx(1.0)


def test_authorization_header_is_exact_bearer(api_key):
    transport = MockTransport()
    transport.queue_response(200, {}, completion_body("ok"))
    provider = make_deepseek(transport)

    provider.generate(ProviderRequest(prompt="hello"))

    assert header_value(transport.calls[0]["headers"], "Authorization") == (
        f"Bearer {DUMMY_SECRET}"
    )


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("https://api.deepseek.com", "https://api.deepseek.com/chat/completions"),
        ("https://api.deepseek.com/", "https://api.deepseek.com/chat/completions"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
        (
            "https://api.deepseek.com/chat/completions",
            "https://api.deepseek.com/chat/completions",
        ),
    ],
)
def test_endpoint_normalisation(api_key, endpoint, expected):
    transport = MockTransport()
    transport.queue_response(200, {}, completion_body("ok"))
    provider = make_deepseek(transport, endpoint=endpoint)

    provider.generate(ProviderRequest(prompt="hello"))

    assert transport.calls[0]["url"] == expected


def test_default_model_used_when_request_omits_it(api_key):
    transport = MockTransport()
    transport.queue_response(
        200, {}, completion_body("ok", model="deepseek-chat")
    )
    provider = make_deepseek(transport, model="")

    provider.generate(ProviderRequest(prompt="hello"))

    assert transport.calls[0]["payload"]["model"] == "deepseek-chat"


# --------------------------------------------------------------------------- #
# DeepSeek: HTTP status and transport failure mapping
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", [401, 403])
def test_auth_statuses_map_to_auth_error(api_key, status):
    transport = MockTransport()
    transport.queue_response(status, {}, b'{"error": "denied"}')
    provider = make_deepseek(transport, max_retries=2)

    with pytest.raises(ProviderAuthError) as info:
        provider.generate(ProviderRequest(prompt="hello"))

    assert len(transport.calls) == 1  # auth failures are never retried
    assert DUMMY_SECRET not in str(info.value)
    assert DUMMY_SECRET not in repr(info.value.describe())


def test_429_maps_to_rate_limit_with_numeric_retry_after(api_key):
    transport = MockTransport()
    transport.queue_response(429, {"Retry-After": "2.5"}, b"{}")
    provider = make_deepseek(transport, max_retries=0)

    with pytest.raises(ProviderRateLimitError) as info:
        provider.generate(ProviderRequest(prompt="hello"))

    assert info.value.retry_after == pytest.approx(2.5)
    assert info.value.retryable is True


def test_429_with_non_numeric_retry_after_has_no_delay(api_key):
    transport = MockTransport()
    transport.queue_response(
        429, {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, b"{}"
    )
    provider = make_deepseek(transport, max_retries=0)

    with pytest.raises(ProviderRateLimitError) as info:
        provider.generate(ProviderRequest(prompt="hello"))

    assert info.value.retry_after is None


def test_500_is_retryable_and_retry_count_is_honoured(api_key):
    transport = MockTransport()
    for _ in range(3):
        transport.queue_response(500, {}, b'{"error": "boom"}')
    provider = make_deepseek(transport, max_retries=2)

    with pytest.raises(ProviderError) as info:
        provider.generate(ProviderRequest(prompt="hello"))

    assert type(info.value) is ProviderError
    assert info.value.retryable is True
    assert len(transport.calls) == 3


def test_transient_500_then_success_returns_response(api_key):
    transport = MockTransport()
    transport.queue_response(503, {}, b"")
    transport.queue_response(200, {}, completion_body("recovered"))
    provider = make_deepseek(transport, max_retries=1)

    response = provider.generate(ProviderRequest(prompt="hello"))

    assert response.text == "recovered"
    assert len(transport.calls) == 2


def test_timeout_maps_to_provider_timeout(api_key):
    transport = MockTransport()
    transport.queue_error(socket.timeout("timed out"))
    provider = make_deepseek(transport, max_retries=0)

    with pytest.raises(ProviderTimeoutError) as info:
        provider.generate(ProviderRequest(prompt="hello"))

    assert info.value.retryable is True


def test_timeout_is_retried_up_to_max_retries(api_key):
    transport = MockTransport()
    for _ in range(3):
        transport.queue_error(socket.timeout("timed out"))
    provider = make_deepseek(transport, max_retries=2)

    with pytest.raises(ProviderTimeoutError):
        provider.generate(ProviderRequest(prompt="hello"))

    assert len(transport.calls) == 3


def test_malformed_json_body_maps_to_response_error(api_key):
    transport = MockTransport()
    transport.queue_response(200, {}, b"<html>not json</html>")
    provider = make_deepseek(transport)

    with pytest.raises(ProviderResponseError):
        provider.generate(ProviderRequest(prompt="hello"))


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b'{"choices": []}',
        b'{"choices": [{"message": {}}]}',
        b'{"choices": [{"message": {"content": null}}]}',
        b"[]",
    ],
)
def test_response_without_content_maps_to_response_error(api_key, body):
    transport = MockTransport()
    transport.queue_response(200, {}, body)
    provider = make_deepseek(transport)

    with pytest.raises(ProviderResponseError):
        provider.generate(ProviderRequest(prompt="hello"))


# --------------------------------------------------------------------------- #
# DeepSeek: streaming
# --------------------------------------------------------------------------- #
def test_streaming_yields_deltas_in_order_and_stops_at_done(api_key):
    transport = MockTransport()
    transport.queue_stream(
        [
            sse_delta("Hel"),
            sse_delta("lo "),
            sse_delta("world"),
            b"data: [DONE]\n\n",
            sse_delta("ignored"),
        ]
    )
    provider = make_deepseek(transport, clock=FakeClock(start=0.0, step=0.01))

    chunks = list(provider.stream(ProviderRequest(prompt="hi")))

    assert chunks == ["Hel", "lo ", "world"]
    assert transport.calls[0]["payload"]["stream"] is True
    assert header_value(transport.calls[0]["headers"], "Accept") == "text/event-stream"
    assert len(transport.stream_frames) == 1


def test_streaming_skips_malformed_frames(api_key):
    transport = MockTransport()
    transport.queue_stream(
        [
            b": keep-alive\n\n",
            b"event: ping\n\n",
            b"data: {this is not json}\n\n",
            sse_delta("ok"),
            b"data: [DONE]\n\n",
        ]
    )
    provider = make_deepseek(transport, clock=FakeClock(start=0.0, step=0.01))

    assert list(provider.stream(ProviderRequest(prompt="hi"))) == ["ok"]


def test_streaming_handles_split_sse_lines(api_key):
    raw = sse_delta("split").decode("utf-8")
    middle = len(raw) // 2
    transport = MockTransport()
    transport.queue_stream(
        [raw[:middle].encode("utf-8"), raw[middle:].encode("utf-8"), b"data: [DONE]\n\n"]
    )
    provider = make_deepseek(transport, clock=FakeClock(start=0.0, step=0.01))

    assert list(provider.stream(ProviderRequest(prompt="hi"))) == ["split"]


# --------------------------------------------------------------------------- #
# DeepSeek: secret containment
# --------------------------------------------------------------------------- #
def test_repr_and_health_do_not_leak_secret(api_key):
    provider = make_deepseek(MockTransport())

    rendered = repr(provider)
    health = provider.health()

    assert DUMMY_SECRET not in rendered
    assert DUMMY_SECRET not in repr(health)
    assert health["provider_id"] == "deepseek"
    assert health["configured"] is True
    assert health["available"] is True
    assert health["detail"] == "ready"


def test_registry_audit_records_never_contain_secret(api_key, tmp_path):
    transport = MockTransport()
    transport.queue_response(200, {}, completion_body("audited"))
    audit = AuditLog(tmp_path / "audit.jsonl")
    registry = ProviderRegistry.from_config(
        Phase9Config(providers=[deepseek_config()]),
        transport=transport,
        audit=audit,
    )

    response = registry.generate(ProviderRequest(prompt="hello"))
    assert response.text == "audited"

    records = list(audit.records())
    assert len(records) == 1
    record = records[0]
    assert record["action"] == "provider.call"
    assert record["outcome"] == "success"
    details = record["details"]
    assert details["provider_id"] == "deepseek"
    assert details["model"] == "deepseek-chat"
    assert details["outcome"] == "success"
    assert isinstance(details["latency_seconds"], (int, float))

    written = audit.path.read_text(encoding="utf-8")
    assert DUMMY_SECRET not in written
    assert "Bearer" not in written


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def test_registry_skips_disabled_providers():
    transport = MockTransport()
    config = Phase9Config(
        providers=[
            deepseek_config(enabled=False),
            mock_config("mock_local"),
        ]
    )
    registry = ProviderRegistry.from_config(config, transport=transport)

    assert [provider.provider_id for provider in registry.enabled()] == ["mock_local"]
    assert "deepseek" not in registry.health_all()
    assert registry.select().provider_id == "mock_local"
    with pytest.raises(KeyError):
        registry.get("deepseek")
    assert transport.calls == []


def test_registry_falls_back_to_second_provider(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    config = Phase9Config(
        providers=[mock_config("mock_primary"), mock_config("mock_secondary")]
    )
    registry = ProviderRegistry.from_config(config, audit=audit)
    registry.get("mock_primary").script_error(
        ProviderError("primary failed", provider_id="mock_primary", retryable=True)
    )
    registry.get("mock_secondary").script("secondary answer")

    response = registry.generate(ProviderRequest(prompt="task"))

    assert response.provider_id == "mock_secondary"
    assert response.text == "secondary answer"
    records = list(audit.records())
    assert [record["outcome"] for record in records] == ["error", "success"]
    assert [record["details"]["provider_id"] for record in records] == [
        "mock_primary",
        "mock_secondary",
    ]
    assert "error_code" in records[0]["details"]


def test_registry_respects_allow_fallback_false():
    config = Phase9Config(
        providers=[mock_config("mock_primary"), mock_config("mock_secondary")]
    )
    registry = ProviderRegistry.from_config(config)
    registry.get("mock_primary").script_error(
        ProviderError("primary failed", provider_id="mock_primary")
    )
    registry.get("mock_secondary").script("secondary answer")

    with pytest.raises(ProviderError):
        registry.generate(ProviderRequest(prompt="task"), allow_fallback=False)

    assert registry.get("mock_secondary").call_count() == 0


def test_registry_raises_when_nothing_eligible():
    config = Phase9Config(providers=[deepseek_config(enabled=False)])
    registry = ProviderRegistry.from_config(config, transport=MockTransport())

    assert registry.enabled() == []
    with pytest.raises(ProviderUnavailableError):
        registry.select()
    with pytest.raises(ProviderUnavailableError):
        registry.generate(ProviderRequest(prompt="hello"))


def test_registry_capability_filter_rejects_ineligible():
    registry = ProviderRegistry.from_config(
        Phase9Config(providers=[mock_config("mock_local")])
    )

    assert registry.select("text_generation").provider_id == "mock_local"
    with pytest.raises(ProviderUnavailableError):
        registry.select("vision")


def test_registry_select_prefers_local_only_when_requested():
    config = Phase9Config(
        providers=[
            mock_config("mock_remote", locality="remote"),
            mock_config("mock_local", locality="local"),
        ]
    )
    registry = ProviderRegistry.from_config(config)

    assert registry.select().provider_id == "mock_remote"
    assert registry.select(prefer_local=True).provider_id == "mock_local"


def test_registry_from_default_config_never_builds_disabled_deepseek():
    # Phase9Config.default() describes DeepSeek (disabled) and the local
    # Ollama backend (enabled).  The disabled remote provider must not be
    # constructed, while the existing local path must still construct without
    # performing any network I/O.
    config = Phase9Config.default(workspace_root=".")

    registry = ProviderRegistry.from_config(config, transport=MockTransport())

    enabled_ids = [provider.provider_id for provider in registry.enabled()]
    assert enabled_ids == ["ollama_local"]
    with pytest.raises(KeyError):
        registry.get("deepseek")


def test_registry_select_excludes_provider_ids():
    config = Phase9Config(
        providers=[mock_config("mock_primary"), mock_config("mock_secondary")]
    )
    registry = ProviderRegistry.from_config(config)

    selected = registry.select(exclude=["mock_primary"])

    assert selected.provider_id == "mock_secondary"


# --------------------------------------------------------------------------- #
# Ollama provider
# --------------------------------------------------------------------------- #
def test_ollama_is_available_returns_false_when_adapter_raises():
    runtime = _FakeRuntime(available=RuntimeError("local service down"))
    provider = OllamaProvider(ollama_config(), runtime=runtime)

    assert provider.is_available() is False
    health = provider.health()
    assert health["configured"] is True
    assert health["available"] is False


def test_ollama_generate_converts_adapter_result():
    runtime = _FakeRuntime(
        result={
            "response": "local answer",
            "eval_count": 4,
            "prompt_eval_count": 2,
            "done_reason": "stop",
            "model": "llama3",
        }
    )
    provider = OllamaProvider(ollama_config(model="llama3"), runtime=runtime)

    response = provider.generate(
        ProviderRequest(prompt="hello", max_tokens=64, temperature=0.5)
    )

    assert response.provider_id == "ollama_local"
    assert response.text == "local answer"
    assert response.model == "llama3"
    assert response.usage == ProviderUsage(
        prompt_tokens=2, completion_tokens=4, total_tokens=6
    )
    assert runtime.calls == [
        {"name": "llama3", "prompt": "hello", "options": {"temperature": 0.5, "num_predict": 64}}
    ]


def test_ollama_missing_model_raises_unavailable():
    provider = OllamaProvider(ollama_config(model=""), runtime=_FakeRuntime(result={}))

    with pytest.raises(ProviderUnavailableError):
        provider.generate(ProviderRequest(prompt="hello"))


def test_ollama_adapter_missing_model_maps_to_unavailable():
    runtime = _FakeRuntime(
        error=RuntimeError("Ollama HTTP 404: model 'ghost' not found")
    )
    provider = OllamaProvider(ollama_config(model="ghost"), runtime=runtime)

    with pytest.raises(ProviderUnavailableError):
        provider.generate(ProviderRequest(prompt="hello"))


def test_ollama_provider_constructs_with_reused_adapter():
    # Constructing the provider imports/uses the main-project adapter without
    # performing any network I/O at construction time.
    provider = OllamaProvider(ollama_config())

    assert isinstance(provider, ModelProviderBase)
    assert provider.provider_id == "ollama_local"
    assert provider.locality == "local"
    assert provider.is_configured() is True


# --------------------------------------------------------------------------- #
# Mock provider
# --------------------------------------------------------------------------- #
def test_mock_provider_is_deterministic():
    provider = MockProvider(provider_id="mock_det")
    request = ProviderRequest(prompt="same prompt", system="system")

    first = provider.generate(request)
    second = provider.generate(request)

    assert first.text == second.text
    assert first.usage == second.usage
    assert first.metadata["offline"] is True
    assert list(provider.stream(request)) == list(provider.stream(request))


def test_mock_provider_uses_scripted_responses_in_order():
    provider = MockProvider()
    provider.script("one")
    provider.script("two")
    request = ProviderRequest(prompt="hello")

    assert provider.generate(request).text == "one"
    assert provider.generate(request).text == "two"
    assert provider.call_count() == 2


# --------------------------------------------------------------------------- #
# MockTransport itself
# --------------------------------------------------------------------------- #
def test_mock_transport_records_get_calls():
    transport = MockTransport()
    transport.queue_response(200, {"X-Test": "1"}, b"ok")

    status, headers, body = transport.get(
        "https://example.invalid/api", {"Accept": "application/json"},
        timeout=1.0, connect_timeout=0.5,
    )

    assert status == 200
    assert headers["X-Test"] == "1"
    assert body == b"ok"
    assert transport.calls[0]["method"] == "GET"
    assert transport.calls[0]["payload"] == {}


# --------------------------------------------------------------------------- #
# UrllibTransport (monkeypatched, no network)
# --------------------------------------------------------------------------- #
def test_urllib_transport_caps_response_body(monkeypatch):
    chunk = b"x" * 65536
    response = _FakeResponse([chunk] * 130)  # > 8 MiB
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None: response
    )
    transport = UrllibTransport()

    with pytest.raises(ProviderResponseError):
        transport.post_json(
            "https://example.invalid/api",
            {"Authorization": "Bearer dummy"},
            {"prompt": "x"},
            timeout=5.0,
            connect_timeout=1.0,
        )


def test_urllib_transport_sends_user_agent_and_connect_timeout(monkeypatch):
    captured = {}
    response = _FakeResponse(
        [b'{"ok": true}'], headers=[("Content-Type", "application/json")]
    )

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    transport = UrllibTransport()
    secret = "dummy-credential-1234567890"

    status, headers, body = transport.post_json(
        "https://example.invalid/v1",
        {"Authorization": f"Bearer {secret}"},
        {"prompt": "x"},
        timeout=9.0,
        connect_timeout=0.5,
    )

    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert body == b'{"ok": true}'
    request = captured["request"]
    assert request.get_header("User-agent") == UrllibTransport.USER_AGENT
    assert captured["timeout"] == pytest.approx(0.5)  # connect timeout honoured
    assert secret not in repr(vars(transport))


def test_urllib_transport_maps_socket_timeout(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise socket.timeout("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    transport = UrllibTransport()

    with pytest.raises(ProviderTimeoutError):
        transport.get(
            "https://example.invalid/api", {}, timeout=5.0, connect_timeout=1.0
        )


def test_urllib_transport_maps_connection_failure(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    transport = UrllibTransport()

    with pytest.raises(ProviderUnavailableError):
        transport.get(
            "https://example.invalid/api", {}, timeout=5.0, connect_timeout=1.0
        )


def _http_error(status: int, body: bytes = b"{}", headers: dict | None = None):
    message = email.message.Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError(
        "https://example.invalid/api", status, "error", message, io.BytesIO(body)
    )


def test_urllib_transport_returns_status_for_http_error(monkeypatch):
    error = _http_error(429, body=b'{"err": 1}', headers={"Retry-After": "3"})

    def fake_urlopen(request, timeout=None):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    transport = UrllibTransport()

    status, headers, body = transport.post_json(
        "https://example.invalid/api", {}, {"prompt": "x"},
        timeout=5.0, connect_timeout=1.0,
    )

    assert status == 429
    assert headers["Retry-After"] == "3"
    assert body == b'{"err": 1}'
