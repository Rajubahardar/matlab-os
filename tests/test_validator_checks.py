"""Validator: tightened RMS check, code pre-check, risky-call detection."""
from matlab_os.validation.engineering_validator import EngineeringValidator as V

RMS_DEMO = (
    "fs = 10000; f = 50; T = 0.1;\n"
    "t = 0:1/fs:T-1/fs;\n"
    "x = sin(2*pi*f*t);\n"
    "rms_value = sqrt(mean(x.^2));\n"
    "plot(t, x);\n"
)


def test_rms_tolerance_is_tight():
    assert V.validate_rms(0.7071067811865476)
    assert not V.validate_rms(0.71)


def test_signal_sample_count_must_match():
    assert V.validate_signal_properties([0.0] * 1000, fs=10000, duration=0.1)
    assert not V.validate_signal_properties([0.0] * 999, fs=10000, duration=0.1)
    assert not V.validate_signal_properties(None, fs=10000, duration=0.1)


def test_demo_script_passes_precheck_and_is_not_risky():
    assert V.validate_matlab_code(RMS_DEMO)
    assert V.find_risky_calls(RMS_DEMO) == []


def test_unbalanced_or_unterminated_code_is_rejected():
    for bad in ("x = [1 2;", "s = 'abc", "x = (1))", 'q = "abc', ""):
        assert not V.validate_matlab_code(bad), bad
    assert not V.validate_matlab_code(None)


def test_transpose_is_not_treated_as_a_string():
    assert V.validate_matlab_code("y = x';")
    assert V.validate_matlab_code("A = [1 2 3]';")
    assert V.validate_matlab_code("s = 'it''s fine';")


def test_risky_calls_are_flagged():
    assert V.find_risky_calls("delete(myfile);") == ["delete"]
    assert V.find_risky_calls("system('dir')") == ["system"]
    assert "clear" in V.find_risky_calls("clear all")
    assert "!" in V.find_risky_calls("!notepad")
    assert "eval" in V.find_risky_calls("eval(cmd)")