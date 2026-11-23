"""Secret redaction utilities for Phase 9.

Single responsibility: given arbitrary text or structured data, produce a copy
that is safe to write to an audit log, an error message or a report.

The redactor is intentionally conservative: it prefers over-redaction to any
chance of leaking a credential.  It never decrypts, never logs, and never
performs network access.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

REDACTED = "[REDACTED]"

#: Substrings that mark an object key as sensitive (case-insensitive).
SECRET_KEY_MARKERS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "api-key",
    "access_key",
    "secret",
    "password",
    "passwd",
    "token",
    "authorization",
    "auth",
    "credential",
    "private_key",
    "session_key",
    "cookie",
)

#: Patterns that identify a credential-shaped literal inside free text.
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Authorization: Bearer <token>
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
    # sk-... style API keys (OpenAI/DeepSeek/Anthropic compatible)
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    # generic "key = value" assignment with a long opaque value
    re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd)"
        r"(\s*[=:]\s*)"
        r"(\"[^\"]{4,}\"|'[^']{4,}'|[^\s,;]{4,})"
    ),
    # "Authorization: Basic <b64>"
    re.compile(r"(?i)\b(basic)\s+[A-Za-z0-9+/=]{8,}"),
    # JSON web token
    re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}"),
)


def is_secret_key(key: Any) -> bool:
    """Return True when *key* names a value that must not be persisted."""
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_KEY_MARKERS)


def scrub_text(text: str) -> str:
    """Return *text* with credential-shaped literals replaced."""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""

    result = text
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.groups >= 3:
            result = pattern.sub(
                lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
                result,
            )
        else:
            result = pattern.sub(REDACTED, result)
    return result


def redact(value: Any, _depth: int = 0) -> Any:
    """Return a secret-free deep copy of *value*.

    Handles mappings, sequences, scalars and objects that expose
    ``to_redacted_dict``.  Anything else is rendered through ``repr`` and then
    scrubbed.  Depth is bounded so cyclic structures cannot hang the call.
    """
    if _depth > 12:
        return "[MAX_DEPTH]"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return scrub_text(value)

    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            name = key if isinstance(key, str) else str(key)
            if is_secret_key(name):
                output[name] = REDACTED
            else:
                output[name] = redact(item, _depth + 1)
        return output

    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item, _depth + 1) for item in value]

    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"

    hook = getattr(value, "to_redacted_dict", None)
    if callable(hook):
        try:
            return redact(hook(), _depth + 1)
        except Exception:  # pragma: no cover - defensive
            return REDACTED

    try:
        return scrub_text(repr(value))[:2000]
    except Exception:  # pragma: no cover - defensive
        return REDACTED


def assert_no_secret_leak(payload: Any, secrets: Iterable[str]) -> None:
    """Raise if any known secret literal appears in *payload*.

    Used by tests and by the audit layer as a last-line defence.
    """
    from ..errors import SecretHandlingError

    rendered = repr(payload)
    for secret in secrets:
        if not secret or len(secret) < 4:
            continue
        if secret in rendered:
            raise SecretHandlingError(
                "Refusing to emit a payload containing a secret literal."
            )


def mask_secret(secret: str | None) -> str:
    """Return a non-reversible, non-usable marker for a configured secret."""
    if not secret:
        return "<unset>"
    if len(secret) <= 4:
        return "<set:%d chars>" % len(secret)
    return "<set:%d chars, tail=%s>" % (len(secret), REDACTED)


__all__ = [
    "REDACTED",
    "SECRET_KEY_MARKERS",
    "is_secret_key",
    "scrub_text",
    "redact",
    "assert_no_secret_leak",
    "mask_secret",
]
