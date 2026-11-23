"""Security primitives for Phase 9 (staging only).

Attributes are resolved lazily (PEP 562) so that ``matlab_os.phase9.audit``
can import ``.security.redaction`` without triggering a circular import through
the approval gate, which itself depends on the audit log.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ApprovalGate",
    "ApprovalRequest",
    "ApprovalState",
    "CAPABILITY_PERMISSIONS",
    "KNOWN_PERMISSIONS",
    "PHASE9_OPERATION",
    "PHASE9_SKILL",
    "PathGuard",
    "PolicyEngine",
    "REDACTED",
    "ResolvedPath",
    "assert_no_secret_leak",
    "canonical_payload_hash",
    "default_permission_manager",
    "is_secret_key",
    "mask_secret",
    "redact",
    "required_permissions",
    "scrub_text",
    "sha256_file",
]

_LAZY = {
    "ApprovalGate": ".approval",
    "ApprovalRequest": ".approval",
    "ApprovalState": ".approval",
    "canonical_payload_hash": ".approval",
    "CAPABILITY_PERMISSIONS": ".policy",
    "KNOWN_PERMISSIONS": ".policy",
    "PHASE9_OPERATION": ".policy",
    "PHASE9_SKILL": ".policy",
    "PolicyEngine": ".policy",
    "default_permission_manager": ".policy",
    "required_permissions": ".policy",
    "PathGuard": ".paths",
    "ResolvedPath": ".paths",
    "sha256_file": ".paths",
    "REDACTED": ".redaction",
    "assert_no_secret_leak": ".redaction",
    "is_secret_key": ".redaction",
    "mask_secret": ".redaction",
    "redact": ".redaction",
    "scrub_text": ".redaction",
}


def __getattr__(name: str) -> Any:  # pragma: no cover - import plumbing
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )
    from importlib import import_module

    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:  # pragma: no cover - introspection helper
    return sorted(set(__all__) | set(globals()))
