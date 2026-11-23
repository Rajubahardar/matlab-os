"""Unit tests for the Phase 9 security foundation.

Covers redaction, the hash-chained audit log, configuration handling and the
workspace path guard.  These are the layers every other component trusts, so
each rejection case is asserted explicitly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from matlab_os.phase9.audit import AuditLog, NullAuditLog, compute_record_hash
from matlab_os.phase9.config import Phase9Config, ProviderConfig
from matlab_os.phase9.errors import (
    ApprovalRejectedError,
    ApprovalRequiredError,
    ConfigurationError,
    OperationLimitError,
    PathSecurityError,
    SecretHandlingError,
    WorkspaceBoundaryError,
    WorkspaceError,
)
from matlab_os.phase9.security.approval import (
    ApprovalGate,
    ApprovalState,
    canonical_payload_hash,
)
from matlab_os.phase9.security.paths import PathGuard
from matlab_os.phase9.security.redaction import (
    REDACTED,
    assert_no_secret_leak,
    is_secret_key,
    mask_secret,
    redact,
    scrub_text,
)

SECRET = "sk-abcdef0123456789abcdef"


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
def test_redact_masks_secret_keys():
    payload = {"api_key": SECRET, "note": "fine"}
    result = redact(payload)
    assert result["api_key"] == REDACTED
    assert result["note"] == "fine"


def test_redact_masks_bearer_tokens_in_text():
    text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
    assert SECRET not in scrub_text(text)
    assert "abcdefghijklmnopqrstuvwxyz" not in scrub_text(text)


def test_redact_masks_sk_prefixed_literals():
    assert "sk-abcdef0123456789" not in scrub_text(
        "the key is sk-abcdef0123456789 here"
    )


def test_redact_handles_nested_structures():
    payload = {
        "outer": [{"password": "hunter2", "keep": 1}],
        "token": "abc",
    }
    result = redact(payload)
    assert result["outer"][0]["password"] == REDACTED
    assert result["outer"][0]["keep"] == 1
    assert result["token"] == REDACTED


def test_redact_never_raises_on_exotic_values():
    result = redact({"value": object()})
    assert isinstance(result["value"], str)


def test_is_secret_key_detects_markers():
    assert is_secret_key("DEEPSEEK_API_KEY")
    assert is_secret_key("Authorization")
    assert not is_secret_key("model_name")


def test_assert_no_secret_leak_raises_on_leak():
    with pytest.raises(SecretHandlingError):
        assert_no_secret_leak({"x": SECRET}, [SECRET])


def test_assert_no_secret_leak_passes_when_clean():
    assert_no_secret_leak({"x": REDACTED}, [SECRET])


def test_mask_secret_does_not_reveal_value():
    masked = mask_secret(SECRET)
    assert SECRET not in masked
    assert masked == "<unset>" or masked.startswith("<set:")


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
def test_audit_appends_and_verifies_chain(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("test.action", "tester", "ok", {"n": 1})
    log.record("test.action", "tester", "ok", {"n": 2})
    result = log.verify_chain()
    assert result["valid"] is True
    assert result["records"] == 2


def test_audit_detects_tampering(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record("a", "tester", "ok", {"n": 1})
    log.record("b", "tester", "ok", {"n": 2})

    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["outcome"] = "tampered"
    lines[0] = json.dumps(record, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert AuditLog(path).verify_chain()["valid"] is False


def test_audit_detects_truncation(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for index in range(3):
        log.record("a", "tester", "ok", {"n": index})

    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[0] + "\n" + lines[2] + "\n", encoding="utf-8")
    assert AuditLog(path).verify_chain()["valid"] is False


def test_audit_continues_chain_after_reopen(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    first = AuditLog(path)
    first.record("a", "tester", "ok", {"n": 1})

    second = AuditLog(path)
    second.record("b", "tester", "ok", {"n": 2})
    assert second.verify_chain()["valid"] is True
    assert second.verify_chain()["records"] == 2


def test_audit_redacts_secret_details(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record("provider.call", "tester", "ok", {"api_key": SECRET})
    content = path.read_text(encoding="utf-8")
    assert SECRET not in content
    assert REDACTED in content


def test_audit_rejects_empty_action(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.jsonl")
    with pytest.raises(Exception):
        log.record("", "tester", "ok", {})


def test_null_audit_log_writes_nothing(tmp_path: Path):
    log = NullAuditLog()
    record = log.record("a", "tester", "ok", {})
    assert record.sequence == 1
    assert log.records() == [] or list(log.records()) == []


def test_compute_record_hash_is_stable():
    payload = {"sequence": 1, "action": "x"}
    assert compute_record_hash(payload) == compute_record_hash(dict(payload))


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def test_default_configuration_disables_deepseek(sandbox: Path):
    cfg = Phase9Config.default(str(sandbox))
    cfg.validate()
    assert cfg.deepseek_enabled() is False
    deepseek = cfg.provider("deepseek")
    assert deepseek is not None
    assert deepseek.enabled is False
    assert deepseek.api_key_env == "DEEPSEEK_API_KEY"


def test_redacted_snapshot_contains_no_credential_values(sandbox: Path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    cfg = Phase9Config.default(str(sandbox))
    snapshot = cfg.redacted_snapshot()
    rendered = json.dumps(snapshot)
    assert SECRET not in rendered
    assert "DEEPSEEK_API_KEY" not in rendered
    deepseek = next(
        item for item in snapshot["providers"] if item["provider_id"] == "deepseek"
    )
    assert deepseek["credential_configured"] is True


def test_provider_resolves_credential_from_environment(sandbox: Path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    cfg = Phase9Config.default(str(sandbox))
    assert cfg.provider("deepseek").resolve_credential() == SECRET
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert cfg.provider("deepseek").resolve_credential() is None


def test_configuration_rejects_unknown_keys(sandbox: Path):
    with pytest.raises(ConfigurationError):
        Phase9Config.from_dict(
            {"unknown_section": {}}, workspace_root=str(sandbox)
        )


def test_configuration_rejects_duplicate_providers(sandbox: Path):
    payload = {
        "providers": [
            {"provider_id": "deepseek", "kind": "deepseek", "enabled": False},
            {"provider_id": "deepseek", "kind": "deepseek", "enabled": False},
        ]
    }
    with pytest.raises(ConfigurationError):
        Phase9Config.from_dict(payload, workspace_root=str(sandbox))


def test_configuration_rejects_bad_timeout(sandbox: Path):
    payload = {
        "providers": [{
            "provider_id": "deepseek",
            "kind": "deepseek",
            "endpoint": "https://api.deepseek.com",
            "timeout_seconds": 5,
            "connect_timeout_seconds": 10,
        }]
    }
    with pytest.raises(ConfigurationError):
        Phase9Config.from_dict(payload, workspace_root=str(sandbox))


def test_environment_overrides_apply(sandbox: Path):
    cfg = Phase9Config.default(str(sandbox))
    cfg.apply_environment({
        "PHASE9_DEEPSEEK_ENABLED": "true",
        "PHASE9_DEEPSEEK_MODEL": "deepseek-reasoner",
        "PHASE9_MAX_CONCURRENT_AGENTS": "3",
    })
    assert cfg.deepseek_enabled() is True
    assert cfg.provider("deepseek").model == "deepseek-reasoner"
    assert cfg.resources.max_concurrent_agents == 3


def test_configuration_requires_explicit_workspace_root():
    with pytest.raises(ConfigurationError):
        Phase9Config.default("")


def test_toml_template_contains_no_secret(sandbox: Path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    cfg = Phase9Config.default(str(sandbox))
    rendered = cfg.to_toml()
    assert SECRET not in rendered
    assert "api_key_env" in rendered


# --------------------------------------------------------------------------- #
# Path guard
# --------------------------------------------------------------------------- #
REJECTED_PATHS = [
    "../escape.txt",
    "a/../../b",
    "/etc/passwd",
    "\\windows\\system32",
    "C:/Windows/system.ini",
    "C:relative.txt",
    "//server/share/file.txt",
    "\\\\server\\share",
    "CON",
    "nul.txt",
    "lpt9",
    "x.txt:stream",
    "trailingdot.",
    "trailing space ",
    "",
    "   ",
    "a" * 300,
    "a\x00b",
    "a\x01b",
    "../".join(["x"] * 40),
]


@pytest.mark.parametrize("candidate", REJECTED_PATHS)
def test_path_guard_rejects_dangerous_forms(guard: PathGuard, candidate):
    with pytest.raises(PathSecurityError):
        guard.resolve(candidate)


def test_path_guard_rejects_too_many_components(sandbox: Path):
    guard = PathGuard(sandbox, max_path_components=3)
    with pytest.raises(PathSecurityError):
        guard.resolve("a/b/c/d")


def test_path_guard_accepts_nested_relative_path(guard: PathGuard):
    resolved = guard.resolve("reports/summary.md")
    assert resolved.relative == "reports/summary.md"
    assert resolved.exists is False


def test_path_guard_reserved_prefix(sandbox: Path):
    guard = PathGuard(sandbox, reserved_prefixes=(".phase9_backups",))
    with pytest.raises(PathSecurityError):
        guard.resolve(".phase9_backups/manifest.jsonl")


def test_path_guard_round_trip_write_read(guard: PathGuard):
    written, digest = guard.atomic_write_text("docs/note.txt", "hello\n")
    assert written.relative == "docs/note.txt"
    text, read_digest = guard.read_text("docs/note.txt")
    assert text == "hello\n"
    assert read_digest == digest


def test_path_guard_enforces_size_limit(sandbox: Path):
    guard = PathGuard(sandbox, max_file_bytes=16)
    with pytest.raises(OperationLimitError):
        guard.atomic_write_text("big.txt", "x" * 64)


def test_path_guard_refuses_write_over_directory(guard: PathGuard):
    guard.ensure_directory("folder")
    with pytest.raises(WorkspaceError):
        guard.atomic_write_text("folder", "data")


def test_path_guard_ensure_directory_is_idempotent(guard: PathGuard):
    first = guard.ensure_directory("a/b")
    second = guard.ensure_directory("a/b")
    assert first.relative == second.relative == "a/b"
    assert second.is_dir


def test_path_guard_remove_file_refuses_directory(guard: PathGuard):
    guard.ensure_directory("folder")
    with pytest.raises(WorkspaceError):
        guard.remove_file("folder")


def test_path_guard_remove_file_removes_regular_file(guard: PathGuard):
    guard.atomic_write_text("temp.txt", "x")
    guard.remove_file("temp.txt")
    assert guard.resolve("temp.txt").exists is False


def _make_directory_link(source: Path, destination: Path) -> str:
    """Create a directory link, returning the mechanism used.

    Windows requires SeCreateSymbolicLinkPrivilege for ``os.symlink`` but
    allows directory *junctions* without elevation.  A junction is a reparse
    point and ``os.path.islink`` reports ``False`` for it, which is exactly why
    the path guard checks ``FILE_ATTRIBUTE_REPARSE_POINT`` instead of relying on
    ``islink``.  Tests therefore exercise the junction mechanism when a
    symlink cannot be created.
    """
    try:
        os.symlink(source, destination, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        pass

    if os.name == "nt":
        import subprocess

        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(destination), str(source)],
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0 and destination.exists():
            return "junction"
    pytest.skip("no directory link mechanism is available on this host")
    raise AssertionError("unreachable")


def test_path_guard_rejects_link_escape(sandbox: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = sandbox / "link"
    mechanism = _make_directory_link(outside, link)

    guard = PathGuard(sandbox)
    with pytest.raises(PathSecurityError):
        guard.resolve("link/secret.txt")
    assert mechanism in {"symlink", "junction"}


def test_path_guard_rejects_reparse_point_even_when_target_is_inside(sandbox: Path):
    inside = sandbox / "real"
    inside.mkdir()
    (inside / "file.txt").write_text("ok", encoding="utf-8")
    link = sandbox / "alias"
    _make_directory_link(inside, link)

    guard = PathGuard(sandbox)
    with pytest.raises(PathSecurityError):
        guard.resolve("alias/file.txt")


def test_path_guard_allows_link_when_configured(sandbox: Path):
    inside = sandbox / "real"
    inside.mkdir()
    (inside / "file.txt").write_text("ok", encoding="utf-8")
    link = sandbox / "alias"
    _make_directory_link(inside, link)

    guard = PathGuard(
        sandbox, allow_symlinks=True, allow_reparse_points=True
    )
    resolved = guard.resolve("alias/file.txt")
    assert resolved.relative == "alias/file.txt"


def test_workspace_boundary_root_must_exist(tmp_path: Path):
    with pytest.raises(WorkspaceBoundaryError):
        PathGuard(tmp_path / "missing")


def test_workspace_boundary_root_must_be_absolute():
    with pytest.raises(WorkspaceBoundaryError):
        PathGuard("relative/path")


# --------------------------------------------------------------------------- #
# Approval gate
# --------------------------------------------------------------------------- #
def test_approval_requires_explicit_decision(approval_gate: ApprovalGate):
    request = approval_gate.request(
        "workspace.write", "a.txt", {"content": "x"}, "agent"
    )
    with pytest.raises(ApprovalRequiredError):
        approval_gate.verify(None, "workspace.write", "a.txt",
                             {"content": "x"}, "agent")
    assert request.state is ApprovalState.PENDING


def test_approval_token_is_single_use(approval_gate: ApprovalGate):
    request = approval_gate.request(
        "workspace.write", "a.txt", {"content": "x"}, "agent"
    )
    approved = approval_gate.approve(request.request_id, "human")
    approval_gate.verify(
        approved.token, "workspace.write", "a.txt", {"content": "x"}, "agent"
    )
    with pytest.raises(ApprovalRejectedError):
        approval_gate.verify(
            approved.token, "workspace.write", "a.txt", {"content": "x"},
            "agent"
        )


def test_approval_rejects_self_approval(approval_gate: ApprovalGate):
    request = approval_gate.request(
        "workspace.write", "a.txt", {"content": "x"}, "agent"
    )
    with pytest.raises(ApprovalRejectedError):
        approval_gate.approve(request.request_id, "agent")


def test_approval_is_bound_to_payload(approval_gate: ApprovalGate):
    request = approval_gate.request(
        "workspace.write", "a.txt", {"content": "original"}, "agent"
    )
    approved = approval_gate.approve(request.request_id, "human")
    with pytest.raises(ApprovalRejectedError):
        approval_gate.verify(
            approved.token, "workspace.write", "a.txt",
            {"content": "different"}, "agent"
        )


def test_approval_is_bound_to_target_and_operation(approval_gate: ApprovalGate):
    request = approval_gate.request(
        "workspace.write", "a.txt", {"content": "x"}, "agent"
    )
    approved = approval_gate.approve(request.request_id, "human")
    with pytest.raises(ApprovalRejectedError):
        approval_gate.verify(
            approved.token, "workspace.delete", "a.txt", {"content": "x"},
            "agent"
        )
    with pytest.raises(ApprovalRejectedError):
        approval_gate.verify(
            approved.token, "workspace.write", "b.txt", {"content": "x"},
            "agent"
        )


def test_approval_expires(audit: AuditLog):
    now = [1000.0]
    gate = ApprovalGate(audit, default_ttl_seconds=10.0, clock=lambda: now[0])
    request = gate.request("workspace.write", "a.txt", {}, "agent",
                           ttl_seconds=5.0)
    approved = gate.approve(request.request_id, "human")
    now[0] += 6.0
    with pytest.raises(ApprovalRejectedError):
        gate.verify(approved.token, "workspace.write", "a.txt", {}, "agent")


def test_approval_rejection_is_terminal(approval_gate: ApprovalGate):
    request = approval_gate.request(
        "workspace.write", "a.txt", {}, "agent", reason="test"
    )
    approval_gate.reject(request.request_id, "human", "not allowed")
    with pytest.raises(ApprovalRejectedError):
        approval_gate.approve(request.request_id, "human")


def test_approval_inventory_counts_states(approval_gate: ApprovalGate):
    first = approval_gate.request("workspace.write", "a.txt", {}, "agent")
    approval_gate.request("workspace.write", "b.txt", {}, "agent")
    approval_gate.approve(first.request_id, "human")
    inventory = approval_gate.inventory()
    assert inventory["approved"] == 1
    assert inventory["pending"] == 1


def test_approval_canonical_hash_is_order_independent():
    assert canonical_payload_hash({"a": 1, "b": 2}) == canonical_payload_hash(
        {"b": 2, "a": 1}
    )


def test_approval_max_ttl_enforced(approval_gate: ApprovalGate):
    with pytest.raises(ValueError):
        approval_gate.request("workspace.write", "a.txt", {}, "agent",
                              ttl_seconds=10_000.0)
