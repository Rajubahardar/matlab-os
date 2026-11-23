"""Shared pytest fixtures for the Phase 9 staging workspace.

Every fixture is hermetic: it operates inside ``tmp_path`` and never touches the
main project, the real workspace, the network or the audit log of another test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

STAGING_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = STAGING_ROOT.parents[1]

for entry in (str(STAGING_ROOT), str(PROJECT_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from matlab_os.phase9.audit import AuditLog  # noqa: E402
from matlab_os.phase9.config import Phase9Config  # noqa: E402
from matlab_os.phase9.security.approval import ApprovalGate  # noqa: E402
from matlab_os.phase9.security.paths import PathGuard  # noqa: E402


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """An isolated workspace root for controlled file operations."""
    root = tmp_path / "sandbox"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def state_root(tmp_path: Path) -> Path:
    """An isolated directory for audit logs and other runtime state."""
    root = tmp_path / "state"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def config(sandbox: Path) -> Phase9Config:
    """Default Phase 9 configuration pointed at the sandbox.

    Approval for writes is disabled in the generic fixture so most tests are
    readable; the approval-specific tests construct their own configuration.
    """
    cfg = Phase9Config.default(str(sandbox))
    cfg.security.require_approval_for_writes = False
    cfg.security.require_approval_for_deletion = False
    cfg.planning.use_model_assistance = False
    return cfg


@pytest.fixture()
def strict_config(sandbox: Path) -> Phase9Config:
    """Configuration with every approval gate enabled."""
    cfg = Phase9Config.default(str(sandbox))
    cfg.security.require_approval_for_writes = True
    cfg.security.require_approval_for_deletion = True
    cfg.planning.use_model_assistance = False
    return cfg


@pytest.fixture()
def audit(state_root: Path) -> AuditLog:
    return AuditLog(state_root / "audit" / "phase9_audit.jsonl")


@pytest.fixture()
def approval_gate(audit: AuditLog) -> ApprovalGate:
    return ApprovalGate(audit, default_ttl_seconds=300.0)


@pytest.fixture()
def guard(sandbox: Path) -> PathGuard:
    return PathGuard(sandbox, max_path_components=16)


@pytest.fixture()
def backups(guard: PathGuard, audit: AuditLog):
    from matlab_os.phase9.workspace.backup import BackupStore

    return BackupStore(guard, retention=10, audit=audit)


@pytest.fixture()
def workspace(config, guard, approval_gate, audit, backups):
    from matlab_os.phase9.workspace.operations import WorkspaceManager

    return WorkspaceManager(
        guard,
        config.security,
        approval_gate=approval_gate,
        audit=audit,
        backups=backups,
    )


@pytest.fixture()
def strict_workspace(strict_config, guard, approval_gate, audit, backups):
    from matlab_os.phase9.workspace.operations import WorkspaceManager

    return WorkspaceManager(
        guard,
        strict_config.security,
        approval_gate=approval_gate,
        audit=audit,
        backups=backups,
    )


@pytest.fixture()
def healthy_monitor():
    """Deterministic telemetry so integration tests do not depend on host load.

    The real :class:`ResourceMonitor` is exercised separately; injecting a
    fixed snapshot makes admission decisions reproducible on a busy machine.
    """
    from matlab_os.intelligence.resource_scheduler import ResourceSnapshot

    class HealthyMonitor:
        def __init__(self) -> None:
            self.samples = 0

        def sample(self, *, force: bool = False):
            self.samples += 1
            return ResourceSnapshot(
                cpu_load_percent=10.0,
                ram_available_gb=12.0,
                logical_cpu_count=8,
                gpu_available=False,
            )

        def age(self):
            return 0.0

        def capabilities(self):
            return {"ram": True, "cpu": True, "logical_cpu": True}

        def unavailable_metrics(self):
            return ()

        def telemetry_available(self):
            return True

        def degraded(self):
            return False

        def reset(self):
            self.samples = 0

    return HealthyMonitor()


@pytest.fixture()
def runtime(config, state_root: Path, healthy_monitor):
    """A fully built Phase 9 runtime backed by the sandbox."""
    from matlab_os.phase9.runtime.orchestrator import Phase9Runtime

    instance = Phase9Runtime.build(
        config, state_root=state_root, monitor=healthy_monitor
    )
    try:
        yield instance
    finally:
        instance.shutdown()
