"""Model and resource requirement validator.

Reuses the existing resource governor for admission and checks that a model
name is available without downloading, replacing or removing anything.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from matlab_os.phase9.resources.governor import ResourceGovernor
from matlab_os.phase9.resources.monitor import ResourceMonitor
from matlab_os.phase9.resources import ResourceRequirements


class ModelNotAvailableError(ValueError):
    pass


class ModelResourceRequirements:
    def __init__(self, model_name: str, ram_gb: float = 0.0, cpu_cores: float = 0.0,
                 gpu_memory_gb: float = 0.0, max_temperature_c: float | None = None):
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name must be a non-empty string.")
        self.model_name = model_name.strip()
        self.ram_gb = ram_gb
        self.cpu_cores = cpu_cores
        self.gpu_memory_gb = gpu_memory_gb
        self.max_temperature_c = max_temperature_c


def validate_model_availability(model_requirements: Mapping[str, Any]) -> dict[str, Any]:
    """Return a model availability report without changing local storage."""
    if not isinstance(model_requirements, Mapping):
        raise TypeError("model_requirements must be a mapping.")
    if "model_name" not in model_requirements:
        raise ValueError("model_requirements must include model_name.")
    model_name = str(model_requirements["model_name"])
    return {
        "model_name": model_name,
        "available": False,
        "status": "unavailable",
        "reason": "unknown model"
    }


def check_model_and_resources(task_id: str, model_requirements: Mapping[str, Any],
                              resource_requirements: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a check report for a model and resource requirement set."""
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string.")
    if not isinstance(model_requirements, Mapping):
        raise TypeError("model_requirements must be a mapping.")
    if "model_name" not in model_requirements:
        raise ValueError("model_requirements must include model_name.")

    model_name = str(model_requirements["model_name"])
    requirements: ResourceRequirements = ResourceRequirements(task_id=task_id)

    if not isinstance(resource_requirements, Mapping):
        return {
            "task_id": task_id,
            "model_name": model_name,
            "available": False,
            "status": "unavailable",
            "reason": "model_name required, no resource requirements supplied"
        }

    if not isinstance(resource_requirements, ResourceRequirements):
        try:
            requirements = ResourceRequirements(
                task_id=task_id,
                ram_gb=float(resource_requirements.get("ram_gb", 0.0)),
                cpu_cores=float(resource_requirements.get("cpu_cores", 0.0)),
                gpu_memory_gb=float(resource_requirements.get("gpu_memory_gb", 0.0)),
                max_temperature_c=resource_requirements.get("max_temperature_c"),
            )
        except (TypeError, ValueError) as exc:
            return {
                "task_id": task_id,
                "model_name": model_name,
                "available": False,
                "status": "unavailable",
                "reason": f"invalid resource_requirements: {exc}"
            }
    else:
        requirements = resource_requirements

    report = {
        "task_id": task_id,
        "model_name": model_name,
        "available": False,
        "status": "unavailable",
        "reason": "not checked",
    }

    # The model registry is the source of truth. Without it, a model must not
    # be treated as available by the ecosystem.
    if not _model_exists(model_name):
        report["reason"] = "model_not_available"
        return report

    # Resource admission is delegated to the existing governor.
    governor = ResourceGovernor(_dummy_resource_config())
    admission = governor.admit(
        task_id=task_id,
        requirements=requirements,
    )
    report.update({
        "available": admission.admitted,
        "status": admission.code,
        "reason": admission.reason,
        "slot": admission.slot,
    })
    return report


def _model_exists(model_name: str) -> bool:
    # The model registry service is outside this package.
    # Without it, the ecosystem must refuse to claim a model is available.
    return False
