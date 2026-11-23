
"""Functional MATLAB and Simulink skill operations."""

import re

SKILL_PROFILES = {
    "MATLAB Programming and Debugging":
        "MATLAB code execution, debugging and workspace inspection.",
    "Simulink Model Development":
        "Simulink model creation, configuration, simulation and inspection.",
    "Power Systems Engineering":
        "Power-system modelling and engineering analysis.",
    "Control Systems Engineering":
        "Control-system modelling and controller design.",
    "Simscape Electrical":
        "Physical electrical-system modelling.",
    "Simulation and Analysis":
        "Simulation execution and result analysis.",
    "Optimization and Parameter Tuning":
        "Parameter optimization and tuning.",
    "Engineering Verification and Validation":
        "Engineering verification and validation.",
    "Research Paper Reproduction":
        "Reproduction of published engineering research.",
    "MATLAB Project Management and Documentation":
        "MATLAB project management and documentation.",
}


def _required(params, *names):
    values = {}
    for name in names:
        value = params.get(name)
        if value is None or str(value).strip() == "":
            raise ValueError("Missing required argument: " + name)
        values[name] = str(value).strip()
    return values


def _matlab_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def _identifier(value, label="name"):
    value = str(value).strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        raise ValueError("Invalid MATLAB " + label + ": " + value)
    return value


def _model(value):
    return _identifier(value, "model name")


def _matlab_operation(operation, p):
    if operation == "workspace":
        return "disp('--- MATLAB WORKSPACE ---'); whos;"

    if operation == "version":
        return "disp(version);"

    if operation == "pwd":
        return "disp(pwd);"

    if operation == "inspect_variable":
        v = _identifier(_required(p, "name")["name"], "variable name")
        return (
            f"if exist('{v}','var'), disp('--- VARIABLE ---'); "
            f"whos('{v}'); disp({v}); "
            f"else, error('Variable {v} does not exist.'); end"
        )

    if operation == "clear_variable":
        v = _identifier(_required(p, "name")["name"], "variable name")
        return f"clear {v}; disp('Cleared variable: {v}');"

    if operation == "run_file":
        path = _required(p, "path")["path"]
        return f"run({_matlab_string(path)});"

    if operation == "check_file":
        path = _required(p, "path")["path"]
        return (
            f"disp(checkcode({_matlab_string(path)}, '-id'));"
        )

    raise ValueError(
        "Unsupported MATLAB operation: " + str(operation)
    )


def _simulink_operation(operation, p):
    if operation == "create_model":
        m = _model(_required(p, "model")["model"])
        return (
            f"if bdIsLoaded({_matlab_string(m)}), "
            f"close_system({_matlab_string(m)},0); end; "
            f"new_system({_matlab_string(m)}); "
            f"open_system({_matlab_string(m)}); "
            f"disp('Created model: {m}');"
        )

    if operation == "open_model":
        m = _model(_required(p, "model")["model"])
        return (
            f"load_system({_matlab_string(m)}); "
            f"open_system({_matlab_string(m)});"
        )

    if operation == "add_block":
        v = _required(p, "model", "source", "destination")
        m = _model(v["model"])
        destination = v["destination"]
        if not destination.startswith(m + "/"):
            raise ValueError("Destination must be inside the selected model.")
        return (
            f"load_system({_matlab_string(m)}); "
            f"add_block({_matlab_string(v['source'])},"
            f"{_matlab_string(destination)},'MakeNameUnique','off');"
        )

    if operation == "set_parameter":
        v = _required(p, "model", "block", "parameter", "value")
        m = _model(v["model"])
        if not v["block"].startswith(m + "/"):
            raise ValueError("Block path must belong to the selected model.")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", v["parameter"]):
            raise ValueError("Invalid Simulink parameter name.")
        return (
            f"set_param({_matlab_string(v['block'])},"
            f"{_matlab_string(v['parameter'])},"
            f"{_matlab_string(v['value'])});"
        )

    if operation == "connect_blocks":
        v = _required(p, "model", "source", "destination")
        m = _model(v["model"])
        for key in ("source", "destination"):
            if not v[key].startswith(m + "/") or "/" not in v[key]:
                raise ValueError(
                    key + " must identify a block and port in the selected model."
                )
        return (
            f"add_line({_matlab_string(m)},"
            f"{_matlab_string(v['source'])},"
            f"{_matlab_string(v['destination'])},"
            "'autorouting','on');"
        )

    if operation == "inspect_model":
        m = _model(_required(p, "model")["model"])
        return (
            f"load_system({_matlab_string(m)}); "
            f"disp(find_system({_matlab_string(m)},"
            "'SearchDepth',1));"
        )

    if operation == "simulate_model":
        m = _model(_required(p, "model")["model"])
        return (
            f"load_system({_matlab_string(m)}); "
            f"simOut = sim({_matlab_string(m)}); "
            "disp('Simulation completed.');"
        )

    if operation == "save_model":
        m = _model(_required(p, "model")["model"])
        path = p.get("path")
        if path:
            return (
                f"save_system({_matlab_string(m)},"
                f"{_matlab_string(path)});"
            )
        return f"save_system({_matlab_string(m)});"

    raise ValueError(
        "Unsupported Simulink operation: " + str(operation)
    )


def build_matlab_code(skill_name, operation, params):
    if skill_name == "MATLAB Programming and Debugging":
        return _matlab_operation(operation, params)

    if skill_name == "Simulink Model Development":
        return _simulink_operation(operation, params)

    if skill_name in {
        "Power Systems Engineering",
        "Control Systems Engineering",
        "Simscape Electrical",
        "Simulation and Analysis",
        "Optimization and Parameter Tuning",
        "Engineering Verification and Validation",
        "Research Paper Reproduction",
        "MATLAB Project Management and Documentation",
    }:
        from .engineering_skill_operations import build_operation
        return build_operation(skill_name, operation, params)

    raise ValueError("No operation builder for this skill yet: " + skill_name)


def execute_skill_handler(agent, skill_name, code=None, **kwargs):
    if not getattr(agent, "initialized", False):
        raise RuntimeError("Initialize MATLABAgent first.")

    if skill_name not in SKILL_PROFILES:
        raise ValueError("Unknown skill: " + skill_name)

    operation = kwargs.pop("operation", None)
    reason = kwargs.pop("reason", None)

    if code is not None and operation is not None:
        raise ValueError("Pass either raw MATLAB code or an operation, not both.")

    if operation is not None:
        code = build_matlab_code(skill_name, operation, kwargs)
    elif code is None:
        raise ValueError(
            "Provide code='...' or operation='...'."
        )

    if not isinstance(code, str) or not code.strip():
        raise ValueError("MATLAB code must be a non-empty string.")

    approval_reason = reason or (
        "MATLAB skill: " + skill_name
        + (". Operation: " + operation if operation else ". Custom MATLAB code.")
    )

    return agent.execute_matlab_code_interactive(
        code,
        reason=approval_reason,
    )


def get_skill_profiles():
    return SKILL_PROFILES.copy()


def get_available_operations():
    return {
        "MATLAB Programming and Debugging": [
            "workspace", "version", "pwd",
            "inspect_variable", "clear_variable",
            "run_file", "check_file"
        ],
        "Simulink Model Development": [
            "create_model", "open_model", "add_block",
            "set_parameter", "connect_blocks",
            "inspect_model", "simulate_model", "save_model"
        ],
        "Power Systems Engineering": [
            "per_unit", "three_phase_fault"
        ],
        "Control Systems Engineering": [
            "state_space_analysis"
        ],
        "Simscape Electrical": [
            "audit_blocks", "inspect_component"
        ],
        "Simulation and Analysis": [
            "signal_metrics", "frequency_metrics"
        ],
        "Optimization and Parameter Tuning": [
            "parameter_sweep"
        ],
        "Engineering Verification and Validation": [
            "validate_signal", "compare_signals"
        ],
        "Research Paper Reproduction": [
            "compare_reference_metrics"
        ],
        "MATLAB Project Management and Documentation": [
            "project_inventory"
        ],
    }


# EXTENDED_ENGINEERING_SKILLS_V1
# Additional skills for the existing MATLABAgent. Execution remains unchanged.
from .extended_engineering_skill_operations import SKILLS as _EXT_SKILLS
from .extended_engineering_skill_operations import build_code as _build_extended_code

_original_build_matlab_code = build_matlab_code
_original_get_available_operations = get_available_operations
_original_get_skill_profiles = get_skill_profiles

def build_matlab_code(skill_name, operation, params=None):
    if skill_name in _EXT_SKILLS:
        return _build_extended_code(skill_name, operation, params or {})
    return _original_build_matlab_code(skill_name, operation, params)

def get_available_operations():
    result = dict(_original_get_available_operations())
    for skill, operations in _EXT_SKILLS.items():
        result[skill] = list(operations.keys())
    return result

def get_skill_profiles():
    result = _original_get_skill_profiles()
    if isinstance(result, dict):
        result = dict(result)
        for skill, operations in _EXT_SKILLS.items():
            result[skill] = {
                "description": "Specialized engineering skill",
                "operations": list(operations.keys())
            }
        return result
    if isinstance(result, (list, tuple)):
        result = list(result)
        for skill, operations in _EXT_SKILLS.items():
            if not any(
                (x == skill) or
                (isinstance(x, dict) and x.get("name", x.get("skill_name")) == skill)
                for x in result
            ):
                result.append({
                    "name": skill,
                    "description": "Specialized engineering skill",
                    "operations": list(operations.keys())
                })
        return result
    raise TypeError("Unsupported skill profile structure.")


# MATLABAGENT_SKILLS_11_15_PROFILE_REGISTRY_V1
# Integrate extension skills into the existing dispatcher registry.
SKILL_PROFILES.update({
    "Electrical Machines Engineering":
        "Electrical machine parameters and equivalent-circuit engineering.",
    "Power Electronics Engineering":
        "Power electronic converter design and PWM analysis.",
    "Renewable Energy and Microgrids":
        "PV array modelling and renewable energy assessment.",
    "Power Quality and Harmonics":
        "Harmonic analysis and power-quality assessment.",
    "Engineering Data Acquisition and Processing":
        "Engineering data import, cleaning, resampling and feature extraction.",
})



# MATLAB_FUNCTION_INTELLIGENCE_SKILL16_V1
# MATLAB Function Intelligence - Skill 16 integration.
from .matlab_function_intelligence import (
    SKILL_NAME as _MFI_SKILL_NAME,
    OPERATIONS as _MFI_OPERATIONS,
    build_code as _mfi_build_code,
)

# Preserve the current dispatcher functions, including Skills 1-15.
_mfi_previous_build_matlab_code = build_matlab_code
_mfi_previous_get_available_operations = get_available_operations
_mfi_previous_get_skill_profiles = get_skill_profiles

SKILL_PROFILES[_MFI_SKILL_NAME] = (
    "MATLAB function discovery, documentation inspection, "
    "workflow generation, controlled execution and output verification."
)

def build_matlab_code(skill_name, operation, params=None):
    if skill_name == _MFI_SKILL_NAME:
        return _mfi_build_code(operation, params or {})
    return _mfi_previous_build_matlab_code(skill_name, operation, params)

def get_available_operations():
    result = dict(_mfi_previous_get_available_operations())
    result[_MFI_SKILL_NAME] = list(_MFI_OPERATIONS.keys())
    return result

def get_skill_profiles():
    result = _mfi_previous_get_skill_profiles()
    if isinstance(result, dict):
        result = dict(result)
        result[_MFI_SKILL_NAME] = SKILL_PROFILES[_MFI_SKILL_NAME]
        return result
    if isinstance(result, (list, tuple)):
        result = list(result)
        if not any(
            item == _MFI_SKILL_NAME
            or (isinstance(item, dict) and
                item.get("name", item.get("skill_name")) == _MFI_SKILL_NAME)
            for item in result
        ):
            result.append(_MFI_SKILL_NAME)
        return result
    raise TypeError("Unsupported skill profile structure after Skill 16 integration.")



# MATLAB_SHARED_CAPABILITY_INTERFACE_V1
# Shared interface for existing and future engineering skills.
def get_matlab_capability_interface(agent):
    from .matlab_capability_interface import get_matlab_capabilities
    return get_matlab_capabilities(agent)

# SHARED_INTELLIGENCE_ENGINE_V1
def get_shared_intelligence(agent=None):
    """Return the Local AI OS shared intelligence service."""
    from matlab_os.intelligence import SharedIntelligence
    return SharedIntelligence(agent=agent)

