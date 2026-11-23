import json
import sys
import time
import traceback
from pathlib import Path
from datetime import datetime, timezone

from matlab_os.agent.matlab_function_intelligence import (
    build_code,
    OPERATIONS,
    SKILL_NAME
)
from matlab_os.agent import skill_handlers

ROOT = Path.cwd()
EVIDENCE = ROOT / "docs" / "engineering" / "skill16_live_validation_20261103_144657.json"

results = []

def record(name, status, detail="", duration=0.0, category="live"):
    results.append({
        "test": name,
        "status": status,
        "category": category,
        "detail": str(detail)[:3000],
        "duration_seconds": round(duration, 3)
    })
    print("[%s] %s: %s" % (status, name, str(detail)[:250]))

def run_test(name, callback, category="live"):
    start = time.time()
    try:
        value = callback()
        record(name, "PASS", repr(value), time.time() - start, category)
        return True, value
    except Exception as exc:
        record(
            name,
            "FAIL",
            "%s: %s\n%s" % (
                type(exc).__name__,
                exc,
                traceback.format_exc()
            ),
            time.time() - start,
            category
        )
        return False, None

# ---------------------------------------------------------
# 1. Static builder validation
# ---------------------------------------------------------

cases = {
    "discover_functions": {
        "function_names": ["eig", "fft", "mean"]
    },
    "search_documentation": {
        "query": "matrix"
    },
    "inspect_function": {
        "function_name": "eig"
    },
    "check_dependencies": {
        "products": ["Simulink"],
        "license_features": ["Simulink"]
    },
    "find_simulink_blocks": {
        "library": "simulink",
        "block_query": "Gain"
    },
    "recommend_function": {
        "query": "matrix eigenvalues"
    },
    "generate_workflow": {
        "steps": [
            {
                "function": "eig",
                "inputs": ["mfi_test_matrix"],
                "output": "mfi_preview_eigenvalues"
            }
        ]
    },
    "execute_workflow": {
        "steps": [
            {
                "function": "eig",
                "inputs": ["mfi_test_matrix"],
                "output": "mfi_test_eigenvalues"
            }
        ]
    },
    "inspect_output": {
        "variable": "mfi_test_eigenvalues"
    },
    "verify_output": {
        "variable": "mfi_test_eigenvalues",
        "expected_class": "double",
        "require_finite": True
    }
}

for operation in OPERATIONS:
    run_test(
        "builder_" + operation,
        lambda op=operation: build_code(op, cases[op]),
        "builder"
    )

# ---------------------------------------------------------
# 2. Dispatcher registration
# ---------------------------------------------------------

def check_registration():
    assert SKILL_NAME in skill_handlers.SKILL_PROFILES
    operations = skill_handlers.get_available_operations()
    assert SKILL_NAME in operations
    assert set(operations[SKILL_NAME]) == set(OPERATIONS)
    return operations[SKILL_NAME]

run_test("dispatcher_registration", check_registration, "integration")

# ---------------------------------------------------------
# 3. MATLABAgent initialization
# ---------------------------------------------------------

agent = None
initialized = False

def initialize_agent():
    global agent, initialized

    from matlab_os.agent.matlab_agent import MATLABAgent

    agent = MATLABAgent()
    result = agent.initialize()

    if getattr(agent, "initialized", False) is not True:
        raise RuntimeError(
            "MATLABAgent.initialize() returned but agent.initialized is false. "
            "Initialization result: %r" % (result,)
        )

    initialized = True
    return "MATLABAgent initialized"

run_test("matlab_agent_initialization", initialize_agent)

# ---------------------------------------------------------
# 4. Existing approval-controlled execution
# ---------------------------------------------------------

def dispatch(operation, **kwargs):
    if not initialized:
        raise RuntimeError("MATLABAgent initialization failed.")

    return skill_handlers.execute_skill_handler(
        agent,
        SKILL_NAME,
        operation=operation,
        reason="Skill 16 live validation: " + operation,
        **kwargs
    )

if initialized:
    # Prepare a known matrix for the workflow.
    # This is a controlled MATLAB statement through the existing
    # interactive execution/approval mechanism.
    run_test(
        "prepare_test_matrix",
        lambda: agent.execute_matlab_code_interactive(
            "mfi_test_matrix = [4 1; 2 3];",
            reason="Skill 16 validation: prepare test matrix"
        )
    )

    live_cases = [
        ("discover_functions", {
            "function_names": ["eig", "fft", "mean"]
        }),
        ("search_documentation", {
            "query": "matrix"
        }),
        ("inspect_function", {
            "function_name": "eig"
        }),
        ("check_dependencies", {
            "products": ["Simulink"],
            "license_features": ["Simulink"]
        }),
        ("find_simulink_blocks", {
            "library": "simulink",
            "block_query": "Gain"
        }),
        ("recommend_function", {
            "query": "matrix eigenvalues"
        }),
        ("generate_workflow", {
            "steps": [
                {
                    "function": "eig",
                    "inputs": ["mfi_test_matrix"],
                    "output": "mfi_preview_eigenvalues"
                }
            ]
        }),
        ("execute_workflow", {
            "steps": [
                {
                    "function": "eig",
                    "inputs": ["mfi_test_matrix"],
                    "output": "mfi_test_eigenvalues"
                }
            ]
        }),
        ("inspect_output", {
            "variable": "mfi_test_eigenvalues"
        }),
        ("verify_output", {
            "variable": "mfi_test_eigenvalues",
            "expected_class": "double",
            "expected_size": [2, 1],
            "require_finite": True
        })
    ]

    for operation, params in live_cases:
        run_test(
            "live_" + operation,
            lambda op=operation, p=params: dispatch(op, **p)
        )
else:
    for operation in OPERATIONS:
        record(
            "live_" + operation,
            "NOT_RUN",
            "MATLABAgent initialization failed; live execution skipped."
        )

# ---------------------------------------------------------
# 5. Evidence report
# ---------------------------------------------------------

counts = {}
for item in results:
    counts[item["status"]] = counts.get(item["status"], 0) + 1

report = {
    "report": "MATLAB Function Intelligence - Skill 16",
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "skill_name": SKILL_NAME,
    "operation_count": len(OPERATIONS),
    "operations": list(OPERATIONS.keys()),
    "matlab_agent_initialized": initialized,
    "previous_skills_retested": False,
    "summary": {
        "total_tests": len(results),
        "passed": counts.get("PASS", 0),
        "failed": counts.get("FAIL", 0),
        "not_run": counts.get("NOT_RUN", 0)
    },
    "results": results
}

EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
EVIDENCE.write_text(
    json.dumps(report, indent=2, ensure_ascii=False),
    encoding="utf-8"
)

print("\n==========================================")
print("SKILL 16 VALIDATION SUMMARY")
print("==========================================")
print("Total tests :", report["summary"]["total_tests"])
print("Passed      :", report["summary"]["passed"])
print("Failed      :", report["summary"]["failed"])
print("Not run     :", report["summary"]["not_run"])
print("MATLAB ready:", initialized)
print("Evidence    :", EVIDENCE)
print("==========================================")

if report["summary"]["failed"] > 0 or report["summary"]["not_run"] > 0:
    sys.exit(1)
