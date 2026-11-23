import sys, json, math, re, inspect, traceback
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))

from matlab_os.agent.matlab_agent import MATLABAgent
from matlab_os.agent import skill_handlers
from matlab_os.intelligence.engine import SharedIntelligence
from matlab_os.intelligence.skill_validation_sandbox import run_candidate_validation

SKILL = "Control Systems Engineering"
OPERATION = "state_space_analysis"

A = [[0.0, 1.0], [-4.0, -1.0]]
B = [[0.0], [1.0]]
C = [[1.0, 0.0]]
D = [[0.0]]

# Independent analytical reference:
# lambda^2 + lambda + 4 = 0
# lambda = -0.5 +/- j*sqrt(15)/2
EXPECTED = {
    "pole_real": -0.5,
    "pole_imag_abs": math.sqrt(15.0) / 2.0,
}

def emit(label, value):
    print(f"\n{label}:")
    print(json.dumps(value, indent=2, default=str))

def extract_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return json.dumps(value, default=str)
    for name in ("output", "stdout", "result", "message", "details"):
        if hasattr(value, name):
            return str(getattr(value, name))
    return str(value)

def execute_approved_matlab(agent, code, reason):
    method = agent.execute_matlab_code_interactive
    sig = inspect.signature(method)
    params = sig.parameters
    kwargs = {}

    if "reason" in params:
        kwargs["reason"] = reason

    return method(code, **kwargs)

def run_registered_operation(agent, A_value, B_value, C_value, D_value):
    # Always route through the original registered skill dispatcher.
    return skill_handlers.execute_skill_handler(
        agent,
        SKILL,
        operation=OPERATION,
        reason="Live validation sandbox: approved state-space test",
        A=A_value,
        B=B_value,
        C=C_value,
        D=D_value,
    )

def parse_numeric_output(value):
    text = extract_text(value)

    # Parse MATLAB's actual complex-pole display:
    # -0.5000 + 1.9365i
    # -0.5000 - 1.9365i
    pattern = (
        r"([-+]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
        r"\s*([+-])\s*"
        r"(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
        r"\s*[iI]"
    )

    matches = re.findall(pattern, text)

    if len(matches) != 2:
        raise ValueError(
            "Expected exactly two displayed complex poles; "
            f"found {len(matches)}."
        )

    poles = []
    for real_text, sign, imag_text in matches:
        real_part = float(real_text)
        imag_part = float(imag_text)
        if sign == "-":
            imag_part = -imag_part

        if not math.isfinite(real_part) or not math.isfinite(imag_part):
            raise ValueError("MATLAB returned a non-finite pole.")

        poles.append((real_part, imag_part))

    # Confirm the two displayed poles form a conjugate pair.
    if not math.isclose(poles[0][0], poles[1][0], abs_tol=1e-8):
        raise ValueError("Pole real parts do not form a conjugate pair.")

    if not math.isclose(
        poles[0][1], -poles[1][1], abs_tol=1e-8
    ):
        raise ValueError("Pole imaginary parts are not conjugates.")

    if poles[0][1] == 0 or poles[1][1] == 0:
        raise ValueError("Expected a complex-conjugate pole pair.")

    return {
        "pole_real": (poles[0][0] + poles[1][0]) / 2.0,
        "pole_imag_abs": (
            abs(poles[0][1]) + abs(poles[1][1])
        ) / 2.0,
    }


def main():
    print("\n==============================================")
    print("MATLABAGENT REAL CANDIDATE VALIDATION")
    print("==============================================")

    agent = MATLABAgent()
    init_result = agent.initialize()
    print("\nMATLABAgent initialization:", init_result)

    if hasattr(agent, "is_initialized") and not agent.is_initialized:
        raise RuntimeError("MATLABAgent did not initialize.")

    intelligence = SharedIntelligence(agent=agent)

    # Create a non-executable documentation candidate.
    skill_content = """# Live State-Space Validation Candidate

## Objective
Validate state-space analysis using an existing registered operation.

## Target Capability
Control Systems Engineering / state_space_analysis

## Operations
- state_space_analysis

## Inputs
- A: state matrix
- B: input matrix
- C: output matrix
- D: feedthrough matrix

## Outputs
- State-space model
- Eigenvalues and stability information

## Engineering Equations and References
- det(lambda*I-A)=0
- lambda^2 + lambda + 4 = 0
- lambda = -0.5 +/- j*sqrt(15)/2

## Validation Criteria
- MATLAB execution succeeds.
- Eigenvalue results match independent analytical references.
- Existing registered operations remain available.

## Safety Constraints
- Candidate source is documentation only.
- Existing MATLAB approval mechanism is mandatory.
- No production skill modification.

## Execution Policy
- Do not execute candidate-supplied code.
- Use only existing approved MATLABAgent operations.

## Lifecycle
- DRAFT -> VALIDATED -> APPROVED -> PUBLISHED
"""

    # Reuse the DRAFT candidate created during the previous run.
    candidate_id = "652c52ce-47cf-4030-a569-753e325bed01"

    print("\nReusing existing candidate:", candidate_id)

    # Verify the candidate exists and is still eligible for validation.
    candidate_dir = intelligence.candidate_dir / candidate_id
    metadata_path = candidate_dir / "metadata.json"

    if not metadata_path.exists():
        raise RuntimeError("Candidate metadata not found.")

    metadata = intelligence._read_json(metadata_path)
    if metadata.get("status") not in ("DRAFT", "VALIDATING"):
        raise RuntimeError(
            "Candidate is not eligible for validation: "
            + str(metadata.get("status"))
        )

    registry = skill_handlers.get_available_operations()
    if SKILL not in registry or OPERATION not in registry[SKILL]:
        raise RuntimeError(
            "Target operation is not registered. "
            "Registered skills: " + str(list(registry.keys()))
        )

    print("Candidate status:", metadata.get("status"))
    print("Target operation:", SKILL, "->", OPERATION)

    # Layer 2: dependencies.
    # Read-only MATLAB dependency checks, still through approval-controlled execution.
    dependency_code = """
assert(exist('ss','file') == 2 || exist('ss','builtin') == 5, ...
    'Control System Toolbox function ss is unavailable.');
assert(exist('eig','file') == 2 || exist('eig','builtin') == 5, ...
    'MATLAB eig function is unavailable.');
disp('DEPENDENCIES_OK');
"""

    # Layer 3: existing registered operation.
    # The operation must return output containing explicit pole markers
    # for the numerical layer; otherwise validation is rejected.
    execution_cases = [{"name": "state_space_registered_operation"}]

    numerical_cases = [{
        "name": "independent_state_space_eigenvalue_reference",
        "expected": EXPECTED,
    }]

    regression_cases = [
        {"name": "state_space_registry_preserved"},
        {"name": "state_space_repeat_execution"},
    ]

    state = {"execution_result": None, "parsed_execution": None, "raw_text": None}

    def dependency_checker(case):
        result = execute_approved_matlab(
            agent,
            dependency_code,
            "Validation sandbox: read-only MATLAB dependency checks",
        )
        text = extract_text(result)
        if "DEPENDENCIES_OK" not in text:
            return {
                "passed": False,
                "details": text,
                "reason": "MATLAB dependency confirmation marker missing.",
            }
        return {"passed": True, "evidence": text}

    def execution_runner(case):
        # Numerical comparison reuses the first real execution result.
        # It must not trigger another MATLAB approval or execution.
        if state.get("parsed_execution") is not None:
            return {
                "success": True,
                "outputs": dict(state["parsed_execution"]),
                "raw_result": state["raw_text"],
                "cached": True,
            }

        result = run_registered_operation(agent, A, B, C, D)
        state["execution_result"] = result
        text = extract_text(result)

        if result is None or not text.strip():
            return {"success": False, "error": "Empty MATLAB operation result."}

        try:
            outputs = parse_numeric_output(result)
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "raw_result": text,
            }

        state["parsed_execution"] = outputs
        state["raw_text"] = text

        return {
            "success": True,
            "outputs": outputs,
            "raw_result": text,
            "cached": False,
        }

    def regression_runner(case):
        registry = skill_handlers.get_available_operations()
        registered = registry.get(SKILL, [])
        if OPERATION not in registered:
            return {
                "passed": False,
                "error": "Existing operation missing from registry.",
                "registered_operations": registered,
            }

        if case["name"] == "state_space_repeat_execution":
            result = run_registered_operation(agent, A, B, C, D)
            if result is None or not extract_text(result).strip():
                return {"passed": False, "error": "Repeat execution returned no output."}

        return {
            "passed": True,
            "evidence": {
                "skill": SKILL,
                "operation": OPERATION,
                "registered": True,
                "production_registry_modified": False,
            },
        }

    plan = {
        "specification": {
            "name": "Live State Space Validation Test",
            "description": "Validation-only candidate.",
            "domain": "control systems",
            "objective": "Validate existing state-space operation.",
            "target_skill": SKILL,
            "target_operation": OPERATION,
            "operations": [OPERATION],
            "inputs": ["A", "B", "C", "D"],
            "outputs": ["pole_real", "pole_imag_abs"],
            "equations": ["lambda^2 + lambda + 4 = 0"],
            "validation_criteria": [
                "MATLAB execution succeeds.",
                "Numerical results match analytical references.",
            ],
            "safety_constraints": [
                "Existing approval mechanism required.",
                "No candidate code execution.",
                "No production registry modification.",
            ],
        },
        "dependency_cases": [{"name": "MATLAB functions ss and eig"}],
        "execution_cases": execution_cases,
        "numerical_cases": numerical_cases,
        "regression_cases": regression_cases,
    }

    result = run_candidate_validation(
        intelligence=intelligence,
        candidate_id=candidate_id,
        validation_plan=plan,
        dependency_checker=dependency_checker,
        execution_runner=execution_runner,
        regression_runner=regression_runner,
        abs_tol=1e-6,
        rel_tol=1e-5,
    )

    emit("LIVE VALIDATION REPORT", result)

    report_path = (
        ROOT / "docs" / "engineering" /
        f"live_candidate_validation_{candidate_id}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result, indent=2, default=str),
        encoding="utf-8",
    )

    print("\nReport saved:", report_path)

    if result.get("passed") is not True:
        print("\nVALIDATION NOT PASSED. Candidate was not advanced.")
        sys.exit(2)

    print("\nALL VALIDATION LAYERS PASSED.")
    print("Candidate status:", result.get("status"))
    print("Automatic publication: NO")
    print("Production skills modified: NO")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nLIVE VALIDATION STOPPED:", type(exc).__name__, str(exc))
        traceback.print_exc()
        sys.exit(1)
