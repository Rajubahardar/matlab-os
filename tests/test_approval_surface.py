"""Task 10.3: human approval surface.

These tests exercise the real queue on disk and the real signing key file.  The
"human" is simulated by calling the same API the CLI calls, with the key file
present, which is exactly what the human's terminal process does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from matlab_os.integration.approval_cli import main as cli_main
from matlab_os.integration.approval_surface import (
    ApprovalSurface,
    ApprovalSurfaceError,
    MutationDescriptor,
    create_key_file,
)
from matlab_os.integration.matlab_policy import MatlabToolPolicy

ARGS = {"model": "m.slx", "scope": "root"}


class Clock:
    def __init__(self, now: float = 500.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def env(tmp_path):
    queue = tmp_path / "queue"
    key = tmp_path / "approval.key"
    create_key_file(key)
    clock = Clock()
    events: list[dict] = []
    surface = ApprovalSurface(
        queue_dir=queue,
        key_file=key,
        policy=MatlabToolPolicy(allow_mutating=True),
        clock=clock,
        audit=events.append,
    )
    return surface, queue, key, clock, events


def descriptor(**overrides) -> MutationDescriptor:
    values = {
        "tool": "model_edit",
        "arguments": ARGS,
        "target": "m.slx",
        "actor": "operator",
        "request_id": "req-1",
    }
    values.update(overrides)
    return MutationDescriptor(**values)


def approved(surface, ticket_source=None):
    record = surface.submit(
        tool="model_edit", arguments=ARGS, actor="operator", request_id="req-1",
        target="m.slx",
    )
    result = surface.decide(
        record["token_id"], approve=True, approver="Raju",
        confirmation=f"APPROVE {record['token_id']}",
    )
    return result["ticket"]


# -- request generation (agent side) ---------------------------------------- #
def test_read_only_tool_cannot_request_approval(env):
    surface, *_ = env
    with pytest.raises(ApprovalSurfaceError, match="read-only"):
        surface.submit(tool="model_read", arguments=ARGS, actor="operator", request_id="r")


def test_unknown_tool_cannot_request_approval(env):
    surface, *_ = env
    with pytest.raises(ApprovalSurfaceError, match="Unclassified"):
        surface.submit(tool="mystery", arguments=ARGS, actor="operator", request_id="r")


def test_submitted_request_is_inspectable(env):
    surface, *_ = env
    record = surface.submit(
        tool="model_edit", arguments=ARGS, actor="operator", request_id="req-1", target="m.slx"
    )
    pending = surface.pending()
    assert len(pending) == 1
    shown = pending[0]
    assert shown["descriptor"]["tool"] == "model_edit"
    assert shown["descriptor"]["arguments"] == ARGS
    assert shown["descriptor"]["digest"] == record["descriptor"]["digest"]


# -- human decision --------------------------------------------------------- #
def test_agent_cannot_approve_without_key_file(tmp_path):
    surface = ApprovalSurface(
        queue_dir=tmp_path / "q",
        key_file=tmp_path / "missing.key",
        policy=MatlabToolPolicy(allow_mutating=True),
    )
    record = surface.submit(
        tool="model_edit", arguments=ARGS, actor="operator", request_id="r1", target="m.slx"
    )
    # Without the key, a ticket cannot even be signed.
    with pytest.raises(ApprovalSurfaceError, match="key not found"):
        surface.decide(
            record["token_id"], approve=True, approver="agent",
            confirmation=f"APPROVE {record['token_id']}",
        )


def test_wrong_confirmation_is_refused(env):
    surface, *_ = env
    record = surface.submit(
        tool="model_edit", arguments=ARGS, actor="operator", request_id="r1", target="m.slx"
    )
    with pytest.raises(ApprovalSurfaceError, match="Confirmation"):
        surface.decide(record["token_id"], approve=True, approver="Raju", confirmation="yes")
    assert surface.pending()[0]["status"] == "pending"


def test_rejection_is_recorded(env):
    surface, *_ = env
    record = surface.submit(
        tool="model_edit", arguments=ARGS, actor="operator", request_id="r1", target="m.slx"
    )
    result = surface.decide(
        record["token_id"], approve=False, approver="Raju", confirmation=""
    )
    assert result["status"] == "rejected"
    assert surface.pending() == []
    with pytest.raises(ApprovalSurfaceError, match="rejected"):
        surface.redeem(descriptor(), f"{record['token_id']}.deadbeef")


# -- redemption ------------------------------------------------------------- #
def test_approved_ticket_redeems_once(env):
    surface, *_ = env
    ticket = approved(surface)
    surface.redeem(descriptor(), ticket)
    with pytest.raises(ApprovalSurfaceError, match="already used"):
        surface.redeem(descriptor(), ticket)


def test_consumption_survives_restart(env):
    surface, queue, key, clock, _events = env
    ticket = approved(surface)
    surface.redeem(descriptor(), ticket)   # consumed before the restart

    restarted = ApprovalSurface(
        queue_dir=queue,
        key_file=key,
        policy=MatlabToolPolicy(allow_mutating=True),
        clock=env[3],
    )
    with pytest.raises(ApprovalSurfaceError, match="already used"):
        restarted.redeem(descriptor(), ticket)


def test_unconsumed_ticket_survives_restart_within_ttl(env):
    surface, queue, key, clock, _events = env
    ticket = approved(surface)

    restarted = ApprovalSurface(
        queue_dir=queue,
        key_file=key,
        policy=MatlabToolPolicy(allow_mutating=True),
        clock=env[3],
    )
    restarted.redeem(descriptor(), ticket)


def test_forged_signature_is_refused(env):
    surface, *_ = env
    ticket = approved(surface)
    token_id, _, sig = ticket.partition(".")
    with pytest.raises(ApprovalSurfaceError, match="signature"):
        surface.redeem(descriptor(), f"{token_id}.{'0' * len(sig)}")


def test_expired_ticket_is_refused(env):
    surface, _, _, clock, _ = env
    ticket = approved(surface)
    clock.advance(surface.ttl_seconds + 1)
    with pytest.raises(ApprovalSurfaceError, match="expired"):
        surface.redeem(descriptor(), ticket)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"arguments": {"model": "m.slx", "scope": "root", "x": 1}}, "arguments_modified"),
        ({"target": "other.slx"}, "target_mismatch"),
        ({"actor": "someone"}, "actor_mismatch"),
        ({"request_id": "req-2"}, "request_mismatch"),
        ({"tool": "model_test"}, "tool_mismatch"),
    ],
)
def test_binding_mismatch_is_refused(env, overrides, reason):
    surface, *_ = env
    ticket = approved(surface)
    with pytest.raises(ApprovalSurfaceError, match=reason):
        surface.redeem(descriptor(**overrides), ticket)


def test_missing_ticket_is_refused(env):
    surface, *_ = env
    with pytest.raises(ApprovalSurfaceError, match="requires human approval"):
        surface.redeem(descriptor(), None)


def test_events_are_audited(env):
    surface, _, _, _, events = env
    ticket = approved(surface)
    surface.redeem(descriptor(), ticket)
    actions = {e["action"] for e in events}
    assert "matlab.approval_surface.submitted" in actions
    assert "matlab.approval_surface.approved" in actions
    assert "matlab.approval_surface.consumed" in actions


# -- CLI -------------------------------------------------------------------- #
def test_cli_init_list_and_approve(tmp_path, capsys, monkeypatch):
    queue = tmp_path / "q"
    key = tmp_path / "k.key"

    assert cli_main(["--queue", str(queue), "--key-file", str(key), "init"]) == 0
    assert key.is_file()

    surface = ApprovalSurface(
        queue_dir=queue, key_file=key, policy=MatlabToolPolicy(allow_mutating=True)
    )
    record = surface.submit(
        tool="model_edit", arguments=ARGS, actor="operator", request_id="req-cli", target="m.slx"
    )

    assert cli_main(["--queue", str(queue), "--key-file", str(key), "pending"]) == 0
    assert record["token_id"] in capsys.readouterr().out

    assert cli_main(
        ["--queue", str(queue), "--key-file", str(key), "show", record["token_id"]]
    ) == 0
    assert "model_edit" in capsys.readouterr().out

    monkeypatch.setattr(
        "builtins.input", lambda _prompt="": f"APPROVE {record['token_id']}"
    )
    assert cli_main(
        [
            "--queue", str(queue), "--key-file", str(key), "approve",
            record["token_id"], "--as", "Raju",
        ]
    ) == 0
    out = capsys.readouterr().out
    assert '"status": "approved"' in out

    ticket = json.loads(out[out.index("{"):])["ticket"]
    reloaded = ApprovalSurface(
        queue_dir=queue, key_file=key, policy=MatlabToolPolicy(allow_mutating=True)
    )
    reloaded.redeem(
        MutationDescriptor(
            tool="model_edit", arguments=ARGS, target="m.slx",
            actor="operator", request_id="req-cli",
        ),
        ticket,
    )
