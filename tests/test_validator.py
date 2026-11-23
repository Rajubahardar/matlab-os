"""Engineering validator: RMS check (theory: 1/sqrt(2) = 0.70710678)."""
from matlab_os.validation.engineering_validator import EngineeringValidator as V


def test_rms_accepts_theoretical_value():
    assert V.validate_rms(0.70710678)


def test_rms_accepts_full_precision_value():
    assert V.validate_rms(0.7071067811865476)


def test_rms_rejects_wrong_values():
    for bad in (0.0, 0.5, 1.0):
        assert not V.validate_rms(bad), bad


def test_rms_rejects_nan():
    assert not V.validate_rms(float("nan"))


def test_matlab_code_syntax_check():
    assert V.validate_matlab_code("x = sin(2*pi*50*t);")
    assert not V.validate_matlab_code("x = (1 + 2;")