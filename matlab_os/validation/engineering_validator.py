"""Engineering validation for MATLAB OS."""
import logging
import math
import re
from typing import Any, List

logger = logging.getLogger(__name__)

# Calls that change files, run other programs, or alter the user's workspace.
# A hit means "needs explicit approval", not "forbidden".
RISKY_CALLS = (
    "delete", "rmdir", "movefile", "copyfile", "mkdir", "system", "unix", "dos",
    "eval", "evalc", "evalin", "assignin", "urlwrite", "websave", "webread",
    "save", "fopen", "fwrite", "clear", "clearvars", "cd",
)


def _balanced(code: str) -> bool:
    """True if brackets match and strings are closed; ignores % comments."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: List[str] = []
    for line in code.splitlines():
        i, n, prev = 0, len(line), ""
        while i < n:
            ch = line[i]
            if ch == "%":
                break
            if ch == '"':
                j = line.find('"', i + 1)
                if j == -1:
                    return False
                i, prev = j + 1, '"'
                continue
            is_transpose = prev != "" and (prev.isalnum() or prev in "_)]}'.")
            if ch == "'" and not is_transpose:
                j = i + 1
                while j < n:
                    if line[j] == "'":
                        if j + 1 < n and line[j + 1] == "'":
                            j += 2
                            continue
                        break
                    j += 1
                else:
                    return False
                i, prev = j + 1, "'"
                continue
            if ch in "([{":
                stack.append(ch)
            elif ch in pairs:
                if not stack or stack.pop() != pairs[ch]:
                    return False
            if not ch.isspace():
                prev = ch
            i += 1
    return not stack


class EngineeringValidator:
    """Validates engineering results and outputs."""

    @staticmethod
    def validate_rms(value: float, expected: float = 0.70710678,
                     tolerance: float = 1e-6) -> bool:
        """Check an RMS value against the theoretical value (1/sqrt(2))."""
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            logger.info("RMS validation: non-numeric or non-finite value")
            return False
        difference = abs(value - expected)
        is_valid = difference <= tolerance
        logger.info(f"RMS validation: computed={value}, expected={expected}, "
                    f"diff={difference:.3e}, valid={is_valid}")
        return is_valid

    @staticmethod
    def validate_signal_properties(signal: Any, fs: float, duration: float) -> bool:
        """The signal must have exactly round(fs * duration) samples."""
        expected_samples = round(fs * duration)
        try:
            actual = len(signal)
        except TypeError:
            return False
        is_valid = actual == expected_samples
        logger.info(f"Signal validation: samples={actual}, "
                    f"expected={expected_samples}, valid={is_valid}")
        return is_valid

    @staticmethod
    def validate_matlab_code(code: str) -> bool:
        """Static pre-check only: non-empty, brackets and strings balanced.

        This does NOT prove MATLAB will parse or run the code.
        """
        return isinstance(code, str) and bool(code.strip()) and _balanced(code)

    @staticmethod
    def find_risky_calls(code: str) -> List[str]:
        """Names of risky MATLAB calls found; any hit needs approval.

        Comments are not stripped, so a hit may be a false positive.
        That errs on the safe side.
        """
        found = {n for n in RISKY_CALLS if re.search(rf"(?<![\w.]){n}\b", code)}
        if re.search(r"^\s*!", code, re.M):
            found.add("!")
        return sorted(found)