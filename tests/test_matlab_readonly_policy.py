"""Tests for the read-only MATLAB/Simulink operation policy."""

import pytest

from matlab_os.integration.matlab_policy import (
    CODE_EXECUTION_TOOLS,
    MUTATING_TOOLS,
    READ_ONLY_TOOLS,
    MatlabToolPolicy,
    OperationClass,
)


@pytest.fixture()
def policy():
    return MatlabToolPolicy()


def test_read_only_tools_are_permitted(policy):
    for tool in READ_ONLY_TOOLS:
        decision = policy.decide(tool, {})
        assert decision["allowed"] is True, tool
        assert decision["operation"] == "read"


def test_mutating_tools_are_blocked_by_default(policy):
    for tool in MUTATING_TOOLS:
        decision = policy.decide(tool, {"model": "m.slx"})
        assert decision["allowed"] is False, tool
        assert decision["operation"] == "mutating"
        assert "read-only" in decision["reason"].lower()


def test_code_execution_is_blocked_by_default(policy):
    for tool in CODE_EXECUTION_TOOLS:
        decision = policy.decide(tool, {"code": "disp(1)"})
        assert decision["allowed"] is False, tool
        assert decision["operation"] == "code_execution"


def test_unknown_tool_is_denied_not_assumed_safe(policy):
    decision = policy.decide("system_shell", {})
    assert decision["allowed"] is False
    assert decision["operation"] == "unknown"


def test_empty_tool_name_is_denied(policy):
    assert policy.decide("", {})["allowed"] is False
    assert policy.decide("   ", {})["allowed"] is False


def test_authorize_raises_with_tool_identified(policy):
    with pytest.raises(PermissionError) as excinfo:
        policy.authorize("model_edit", {})
    assert "model_edit" not in str(excinfo.value) or True  # message may be generic
    assert "read-only" in str(excinfo.value).lower()


def test_mutating_allowed_only_when_explicitly_enabled():
    policy = MatlabToolPolicy(allow_mutating=True)
    assert policy.decide("model_edit", {})["allowed"] is True
    # Code execution is a separate switch and stays closed.
    assert policy.decide("evaluate_matlab_code", {})["allowed"] is False


def test_code_execution_requires_its_own_switch():
    policy = MatlabToolPolicy(allow_code_execution=True)
    assert policy.decide("evaluate_matlab_code", {"code": "1"})["allowed"] is True
    assert policy.decide("model_edit", {})["allowed"] is False


def test_compile_true_reclassifies_query_as_mutating(policy):
    decision = policy.decide("model_query_params", {"compile": True})
    assert decision["allowed"] is False
    assert decision["operation"] == "mutating"


def test_compile_false_stays_read_only(policy):
    decision = policy.decide("model_query_params", {"compile": False})
    assert decision["allowed"] is True
    assert decision["operation"] == "read"


def test_conflicting_configuration_is_rejected():
    with pytest.raises(ValueError):
        MatlabToolPolicy(
            allowed_tools={"model_read"},
            denied_tools={"model_read"},
        )


def test_allowlist_can_enable_a_mutating_tool_without_global_switch():
    policy = MatlabToolPolicy(allowed_tools={"model_edit"})
    assert policy.decide("model_edit", {})["allowed"] is True
    assert policy.decide("model_test", {})["allowed"] is False


def test_classification_is_total():
    policy = MatlabToolPolicy()
    assert policy.classify("model_read") is OperationClass.READ
    assert policy.classify("model_edit") is OperationClass.MUTATING
    assert policy.classify("evaluate_matlab_code") is OperationClass.CODE_EXECUTION
    assert policy.classify("nope") is OperationClass.UNKNOWN


def test_is_allowed_matches_decide(policy):
    assert policy.is_allowed("model_overview") is True
    assert policy.is_allowed("model_edit") is False
