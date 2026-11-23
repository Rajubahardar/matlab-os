"""Phase 9 model-provider layer (staging only).

Public surface of the provider package.  Importing this module performs no I/O,
no network access and no model loading; the reused main-project Ollama adapter
is imported lazily only when an ``OllamaProvider`` is constructed.
"""

from __future__ import annotations

from .base import (
    Clock,
    ModelProviderBase,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)
from .deepseek import DeepSeekProvider
from .mock import MockProvider
from .ollama import OllamaProvider
from .registry import ProviderRegistry
from .transport import (
    HttpTransport,
    MAX_RESPONSE_BYTES,
    MockTransport,
    StatusAndBody,
    UrllibTransport,
    decode_body,
    error_for_status,
    parse_retry_after,
)

__all__ = [
    "Clock",
    "DeepSeekProvider",
    "HttpTransport",
    "MAX_RESPONSE_BYTES",
    "MockProvider",
    "MockTransport",
    "ModelProviderBase",
    "OllamaProvider",
    "ProviderRegistry",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderUsage",
    "StatusAndBody",
    "UrllibTransport",
    "decode_body",
    "error_for_status",
    "parse_retry_after",
]
