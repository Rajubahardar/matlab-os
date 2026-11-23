"""C2: per-operation human approval for MATLAB/Simulink mutations.

Every rejection path is asserted to raise before the transport is reached, so a
denied mutation provably has no side effect.
"""

from __future__ import annotations

from dataclasses import replace

import json
from pathlib import Path

import pytest

from matlab_os.integration.matlab_approval import (
    ApprovalAuthority,
    ApprovalError,
    MutationRequest,
)
from matlab_os.integration.matlab_policy import (
    MatlabToolPolicy,
    OperationClass,
    classify_matlab_code,
)
from matlab_os.orchestration.control_plane import build_control_plane
from matlab_os.phase9.config import Phase9Config


def pinned_config(root):
    """Pin resource budgets so admission does not depend on host CPU load."""
    base = Phase9Config.default(str(root))
    return replace(
        base,
        resources=replace(
            base.resources,
            max_cpu_load_percent=100.0,
            min_free_ram_gb=0.0,
            fail_closed_without_telemetry=False,
        ),
    )


class _DeterministicMonitor:
    """A test-only resource monitor that reports ample, stable resources.

    This keeps the control-plane approval tests deterministic on hosts where the
    stdlib RAM reading is low or volatile, without changing the production
    governor's safety checks.
    """

    def __init__(self, *, ram_available_gb: float = 8.0, cpu_load_percent: float = 5.0) -> None:
        self._ram = float(ram_available_gb)
        self._cpu = float(cpu_load_percent)
        self._capabilities = {"ram": True, "cpu": True, "logical_cpu": True}
        self._age = 0.0

    def sample(self, force: bool = False) -> Any:
        from matlab_os.intelligence.resource_scheduler import ResourceSnapshot
        self._age = 0.05
        return ResourceSnapshot(
            cpu_load_percent=self._cpu,
            ram_available_gb=self._ram,
            logical_cpu_count=8,
            timestamp=1000.0 + self._age,
        )

    def capabilities(self) -> dict[str, bool]:
        return dict(self._capabilities)

    def age(self) -> float:
        return self._age


def deterministic_monitor() -> _DeterministicMonitor:
    """Return a monitor that reports ample resources for approval tests."""
    return _DeterministicMonitor()


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_request(**overrides) -> MutationRequest:
    values = {
        "tool": "model_edit",
        "arguments": {"model": "m.slx", "scope": "root", "op": "add_block"},
        "target": "m.slx",
        "actor": "raju",
        "request_id": "req-1",
    }
    values.update(overrides)
    return MutationRequest(**values)


@pytest.fixture()
def authority():
    clock = Clock()
    events: list[dict] = []
    auth = ApprovalAuthority(clock=clock, audit=events.append)
    return auth, clock, events


# -- policy classification -------------------------------------------------- #
@pytest.mark.parametrize(
    "code",
    ["version", "ver", "pwd", "whos", "disp(2+2)", "disp(2+2);", "  ver  "],
)
def test_side_effect_free_code_is_read_only(code):
    assert classify_matlab_code(code) is OperationClass.READ


@pytest.mark.parametrize(
    "code",
    [
        "delete('x')",
        "disp(2+2); delete('x')",
        "save('m.slx')",
        "system('calc')",
        "disp(2+2)\ndelete('x')",
        "",
        None,
        123,
        "x" * 500,
    ],
)
def test_other_code_is_execution(code):
    assert classify_matlab_code(code) is OperationClass.CODE_EXECUTION


def test_code_execution_tool_never_becomes_read_only():
    policy = MatlabToolPolicy(allow_code_execution=True)
    # The tool can run anything, so classification never softens.  Only the
    # approval requirement distinguishes safe expressions from arbitrary code.
    assert policy.classify(
        "evaluate_matlab_code", {"code": "ver"}
    ) is OperationClass.CODE_EXECUTION
    assert policy.classify(
        "evaluate_matlab_code", {"code": "delete('x')"}
    ) is OperationClass.CODE_EXECUTION

    safe = policy.decide("evaluate_matlab_code", {"code": "ver"})
    unsafe = policy.decide("evaluate_matlab_code", {"code": "delete('x')"})
    assert safe["allowed"] is True and safe["requires_approval"] is False
    assert unsafe["allowed"] is True and unsafe["requires_approval"] is True


def test_code_execution_is_denied_without_the_flag_regardless_of_code():
    policy = MatlabToolPolicy()
    for code in ("ver", "delete('x')"):
        decision = policy.decide("evaluate_matlab_code", {"code": code})
        assert decision["allowed"] is False, code

    # Arbitrary code would also need approval if the flag were ever enabled;
    # a side-effect-free expression would need only the flag.
    safe = policy.decide("evaluate_matlab_code", {"code": "ver"})
    unsafe = policy.decide("evaluate_matlab_code", {"code": "delete('x')"})
    assert safe["requires_approval"] is False
    assert unsafe["requires_approval"] is True


# -- lifecycle -------------------------------------------------------------- #
def test_read_only_tool_needs_no_approval(authority):
    auth, _, _ = authority
    with pytest.raises(ApprovalError):
        auth.request_approval(make_request(tool="model_read"))


def test_unknown_tool_cannot_request_approval(authority):
    auth, _, _ = authority
    with pytest.raises(ApprovalError):
        auth.request_approval(make_request(tool="totally_unknown"))


def test_happy_path_is_single_use(authority):
    auth, _, events = authority
    pending = auth.request_approval(make_request())
    ticket = auth.approve(pending.token_id, "raju")

    auth.authorize(make_request(), ticket)

    with pytest.raises(ApprovalError, match="already been used"):
        auth.authorize(make_request(), ticket)

    assert any(e["action"] == "matlab.approval.consumed" for e in events)


def test_missing_ticket_is_refused(authority):
    auth, _, _ = authority
    auth.request_approval(make_request())
    with pytest.raises(ApprovalError, match="requires explicit human approval"):
        auth.authorize(make_request(), None)


def test_ungranted_request_cannot_be_redeemed(authority):
    auth, _, _ = authority
    pending = auth.request_approval(make_request())
    with pytest.raises(ApprovalError, match="never granted"):
        auth.authorize(make_request(), f"{pending.token_id}.deadbeef")


def test_forged_signature_is_refused(authority):
    auth, _, _ = authority
    pending = auth.request_approval(make_request())
    real = auth.approve(pending.token_id, "raju")
    token_id, _, signature = real.partition(".")
    forged = f"{token_id}.{('0' * len(signature))}"
    with pytest.raises(ApprovalError, match="signature"):
        auth.authorize(make_request(), forged)


def test_unknown_ticket_is_refused(authority):
    auth, _, _ = authority
    with pytest.raises(ApprovalError, match="Unknown approval ticket"):
        auth.authorize(make_request(), "nope.abc")


def test_empty_approver_is_refused(authority):
    auth, _, _ = authority
    pending = auth.request_approval(make_request())
    with pytest.raises(ApprovalError, match="approver identity"):
        auth.approve(pending.token_id, "   ")


def test_expired_ticket_is_refused(authority):
    auth, clock, _ = authority
    pending = auth.request_approval(make_request())
    ticket = auth.approve(pending.token_id, "raju")
    clock.advance(auth.ttl_seconds + 1)
    with pytest.raises(ApprovalError, match="expired"):
        auth.authorize(make_request(), ticket)


def test_expired_request_cannot_be_approved(authority):
    auth, clock, _ = authority
    pending = auth.request_approval(make_request())
    clock.advance(auth.ttl_seconds + 1)
    with pytest.raises(ApprovalError, match="expired"):
        auth.approve(pending.token_id, "raju")


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"arguments": {"model": "m.slx", "scope": "root", "op": "delete_block"}}, "arguments_modified"),
        ({"target": "other.slx"}, "target_mismatch"),
        ({"actor": "someone-else"}, "actor_mismatch"),
        ({"request_id": "req-2"}, "request_mismatch"),
        ({"tool": "model_test"}, "tool_mismatch"),
    ],
)
def test_binding_mismatches_are_refused(authority, overrides, reason):
    auth, _, _ = authority
    pending = auth.request_approval(make_request())
    ticket = auth.approve(pending.token_id, "raju")
    with pytest.raises(ApprovalError, match=reason):
        auth.authorize(make_request(**overrides), ticket)


def test_refusal_does_not_consume_the_ticket(authority):
    auth, _, _ = authority
    pending = auth.request_approval(make_request())
    ticket = auth.approve(pending.token_id, "raju")

    with pytest.raises(ApprovalError):
        auth.authorize(make_request(target="elsewhere.slx"), ticket)

    # The genuine operation can still proceed: a mismatched attempt must not
    # burn the operator's approval.
    auth.authorize(make_request(), ticket)


def test_pending_and_history_are_inspectable(authority):
    auth, _, _ = authority
    pending = auth.request_approval(make_request())
    assert auth.pending()[0]["token_id"] == pending.token_id
    auth.approve(pending.token_id, "raju")
    assert auth.history()[0]["approver"] == "raju"
    assert auth.history()[0]["consumed"] is False


# -- control plane integration ---------------------------------------------- #
class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, tool_name, arguments):
        self.calls.append((tool_name, dict(arguments)))
        return {"content": [{"type": "text", "text": "ok"}]}


def approved_ticket(control, tool, arguments, *, actor="operator", request_id="r-1"):
    from matlab_os.integration.matlab_approval import approval_target

    request = MutationRequest(
        tool=tool,
        arguments=arguments,
        target=approval_target(arguments, control.workspace_root),
        actor=actor,
        request_id=request_id,
    )
    pending = control.approval.request_approval(request)
    return control.approval.approve(pending.token_id, "raju")


def test_mutating_decision_always_requires_approval():
    policy = MatlabToolPolicy(allow_mutating=True)
    decision = policy.decide("model_edit", {"model": "m.slx"})
    assert decision["allowed"] is True
    assert decision["requires_approval"] is True

    read = policy.decide("model_read", {"model": "m.slx"})
    assert read["allowed"] is True
    assert read["requires_approval"] is False


def test_mutation_without_approval_never_reaches_transport(tmp_path):
    recorder = Recorder()
    control = build_control_plane(
        tmp_path / "ws",
        transport=recorder,
        allow_mutating=True,
        config=pinned_config(tmp_path / "ws"),
        monitor=deterministic_monitor(),
    )
    result = control.run(
        "Edit the model",
        matlab_tool="model_edit",
        matlab_arguments={"model": "m.slx"},
        request_id="r-1",
    )
    assert str(result.status) != "COMPLETED"
    assert recorder.calls == [] 


def test_mutation_with_approval_executes_exactly_once(tmp_path):
    recorder = Recorder()
    control = build_control_plane(
        tmp_path / "ws",
        transport=recorder,
        allow_mutating=True,
        config=pinned_config(tmp_path / "ws"),
        monitor=deterministic_monitor(),
    )
    arguments = {"model": "m.slx", "scope": "root"}
    ticket = approved_ticket(control, "model_edit", arguments)

    result = control.run(
        "Edit the model",
        matlab_tool="model_edit",
        matlab_arguments=arguments,
        request_id="r-1",
        approval_ticket=ticket,
    )

    assert str(result.status) == "COMPLETED", result.to_dict()
    assert recorder.calls == [("model_edit", arguments)]


def test_modified_arguments_are_refused_before_transport(tmp_path):
    recorder = Recorder()
    control = build_control_plane(
        tmp_path / "ws",
        transport=recorder,
        allow_mutating=True,
        config=pinned_config(tmp_path / "ws"),
        monitor=deterministic_monitor(),
    )
    ticket = approved_ticket(control, "model_edit", {"model": "m.slx"})

    result = control.run(
        "Edit the model",
        matlab_tool="model_edit",
        matlab_arguments={"model": "m.slx", "extra": "sneaky"},
        request_id="r-1",
        approval_ticket=ticket,
    )
    assert str(result.status) != "COMPLETED"
    assert recorder.calls == []


def test_approval_cannot_be_replayed_across_requests(tmp_path):
    recorder = Recorder()
    control = build_control_plane(
        tmp_path / "ws",
        transport=recorder,
        allow_mutating=True,
        config=pinned_config(tmp_path / "ws"),
        monitor=deterministic_monitor(),
    )
    arguments = {"model": "m.slx"}
    ticket = approved_ticket(control, "model_edit", arguments)

    control.run(
        "Edit the model",
        matlab_tool="model_edit",
        matlab_arguments=arguments,
        request_id="r-1",
        approval_ticket=ticket,
    )
    control.run(
        "Edit the model again",
        matlab_tool="model_edit",
        matlab_arguments=arguments,
        request_id="r-1",
        approval_ticket=ticket,
    )
    assert recorder.calls == [("model_edit", arguments)]


def test_code_execution_cannot_bypass_approval(tmp_path):
    recorder = Recorder()
    control = build_control_plane(
        tmp_path / "ws",
        transport=recorder,
        allow_mutating=True,
        allow_code_execution=True,
        config=pinned_config(tmp_path / "ws"),
        monitor=deterministic_monitor(),
    )
    result = control.run(
        "Run some code",
        matlab_tool="evaluate_matlab_code",
        matlab_arguments={"code": "delete('important.m')"},
        request_id="r-9",
    )
    assert str(result.status) != "COMPLETED"
    assert recorder.calls == []


def test_read_only_code_runs_without_approval_when_enabled(tmp_path):
    recorder = Recorder()
    control = build_control_plane(
        tmp_path / "ws",
        transport=recorder,
        allow_code_execution=True,
        config=pinned_config(tmp_path / "ws"),
        monitor=deterministic_monitor(),
    )
    result = control.run(
        "Check the version",
        matlab_tool="evaluate_matlab_code",
        matlab_arguments={"code": "disp(2+2);"},
        request_id="r-10",
    )
    assert str(result.status) == "COMPLETED", result.to_dict()
    assert recorder.calls == [("evaluate_matlab_code", {"code": "disp(2+2);"})] 


def test_approval_events_are_audited(tmp_path):
    recorder = Recorder()
    control = build_control_plane(
        tmp_path / "ws",
        transport=recorder,
        allow_mutating=True,
        config=pinned_config(tmp_path / "ws"),
        monitor=deterministic_monitor(),
    )
    arguments = {"model": "m.slx"}
    ticket = approved_ticket(control, "model_edit", arguments)
    control.run(
        "Edit the model",
        matlab_tool="model_edit",
        matlab_arguments=arguments,
        request_id="r-1",
        approval_ticket=ticket,
    )

    audit = Path(control.runtime.state_root) / "audit" / "phase9_audit.jsonl"
    records = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    actions = {r.get("action") for r in records}
    assert "matlab.approval.requested" in actions
    assert "matlab.approval.granted" in actions
    assert "matlab.approval.consumed" in actions


def test_denied_approval_attempts_are_audited(tmp_path):
    recorder = Recorder()
    control = build_control_plane(
        tmp_path / "ws",
        transport=recorder,
        allow_mutating=True,
        config=pinned_config(tmp_path / "ws"),
        monitor=deterministic_monitor(),
    )
    control.run(
        "Edit without approval",
        matlab_tool="model_edit",
        matlab_arguments={"model": "m.slx"},
        request_id="r-77",
    )
    audit = Path(control.runtime.state_root) / "audit" / "phase9_audit.jsonl"
    records = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    refusals = [
        r for r in records if r.get("action") == "matlab.approval.refused"
    ]
    assert refusals, records
    assert refusals[0]["details"]["reason"] == "missing_ticket"
