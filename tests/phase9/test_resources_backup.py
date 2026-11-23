"""Tests for the Phase 9 resource monitor, resource governor and backup store.

The tests are deterministic: telemetry is injected through a minimal monitor
double, the clock is injectable, and every filesystem access goes through a
:class:`PathGuard` rooted at ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import sys
import threading

import pytest

from matlab_os.intelligence.resource_scheduler import (
    ResourceRequirements,
    ResourceScheduler,
    ResourceSnapshot,
    TaskState,
)

from matlab_os.phase9.audit import AuditLog
from matlab_os.phase9.config import ResourceConfig
from matlab_os.phase9.errors import (
    BackupError,
    ConcurrencyLimitError,
    IntegrityError,
    PathSecurityError,
    ResourceExhaustedError,
    TelemetryUnavailableError,
    WorkspaceError,
)
from matlab_os.phase9.resources import (
    Admission,
    ResourceGovernor,
    ResourceMonitor,
)
from matlab_os.phase9.resources.monitor import (
    CONSERVATIVE_CPU_LOAD,
    CONSERVATIVE_RAM_AVAILABLE_GB,
    UNAVAILABLE,
)
from matlab_os.phase9.security.paths import PathGuard, ResolvedPath
from matlab_os.phase9.workspace import BackupRecord, BackupStore
from matlab_os.phase9.workspace.backup import DEFAULT_DIRECTORY, MANIFEST_NAME


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class ManualClock:
    """Deterministic, manually advanced clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self.value = float(start)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


class StaticMonitor:
    """Minimal monitor double returning one scripted telemetry result."""

    def __init__(
        self,
        snapshot: ResourceSnapshot | None = None,
        capabilities: dict[str, bool] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.snapshot = snapshot
        self._capabilities = (
            dict(capabilities)
            if capabilities is not None
            else {"ram": True, "cpu": True, "logical_cpu": True}
        )
        self.error = error
        self.sample_calls = 0

    def sample(self, *, force: bool = False) -> ResourceSnapshot:
        self.sample_calls += 1
        if self.error is not None:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot

    def capabilities(self) -> dict[str, bool]:
        return dict(self._capabilities)


class BrokenRamMonitor(ResourceMonitor):
    """Real monitor whose RAM sampler always fails."""

    def _sample_ram(self) -> tuple[float, float]:
        raise OSError("RAM telemetry is not available on this test double")


class OutOfRangeCpuMonitor(ResourceMonitor):
    """Real monitor whose CPU sampler returns an out-of-range value."""

    def _sample_cpu(self) -> float:
        return -42.0


def healthy_snapshot(
    cpu: float = 5.0,
    ram: float = 8.0,
    cpus: int = 8,
) -> ResourceSnapshot:
    """Return a telemetry snapshot describing an idle 16 GB / 8 CPU host."""
    return ResourceSnapshot(
        cpu_load_percent=cpu,
        ram_available_gb=ram,
        logical_cpu_count=cpus,
    )


def build_governor(
    monitor: StaticMonitor | None = None,
    **config_overrides: object,
) -> tuple[ResourceGovernor, StaticMonitor, ResourceScheduler]:
    """Build a governor with injected monitor and a real (reused) scheduler."""
    config = ResourceConfig()
    for key, value in config_overrides.items():
        setattr(config, key, value)

    if monitor is None:
        monitor = StaticMonitor(healthy_snapshot())

    scheduler = ResourceScheduler(
        max_concurrent=config.max_concurrent_agents,
        max_cpu_load_percent=config.max_cpu_load_percent,
        minimum_free_ram_gb=config.min_free_ram_gb,
        thermal_limit_c=config.thermal_limit_c,
    )
    governor = ResourceGovernor(config, monitor=monitor, scheduler=scheduler)
    return governor, monitor, scheduler


@pytest.fixture()
def guard(tmp_path) -> PathGuard:
    root = tmp_path / "workspace"
    root.mkdir()
    return PathGuard(root)


@pytest.fixture()
def store(guard: PathGuard) -> BackupStore:
    return BackupStore(guard, retention=50)


# --------------------------------------------------------------------------- #
# ResourceMonitor
# --------------------------------------------------------------------------- #
def test_monitor_sample_returns_valid_snapshot() -> None:
    monitor = ResourceMonitor(sample_seconds=0.5, max_age_seconds=5.0)
    snapshot = monitor.sample()

    assert isinstance(snapshot, ResourceSnapshot)
    assert 0.0 <= snapshot.cpu_load_percent <= 100.0
    assert snapshot.ram_available_gb >= 0.0
    assert snapshot.logical_cpu_count >= 1
    assert isinstance(snapshot.timestamp, float)


def test_monitor_caches_sample_inside_max_age() -> None:
    clock = ManualClock()
    monitor = ResourceMonitor(
        sample_seconds=1.0, max_age_seconds=15.0, clock=clock
    )

    first = monitor.sample()
    clock.advance(1.0)
    second = monitor.sample()

    # The cached object is returned unchanged: no second OS sampling happened.
    assert second is first
    assert monitor.age() == pytest.approx(1.0, abs=1e-9)

    clock.advance(20.0)
    third = monitor.sample()

    assert third is not first
    assert third.timestamp == pytest.approx(clock())
    assert monitor.age() == pytest.approx(0.0, abs=1e-9)


def test_monitor_force_resamples_inside_max_age() -> None:
    monitor = ResourceMonitor(sample_seconds=1.0, max_age_seconds=15.0)
    first = monitor.sample()
    forced = monitor.sample(force=True)

    assert forced is not first
    assert isinstance(forced, ResourceSnapshot)


def test_monitor_capabilities_shape() -> None:
    monitor = ResourceMonitor(sample_seconds=0.5, max_age_seconds=5.0)

    before = monitor.capabilities()
    assert set(before) == {"ram", "cpu", "logical_cpu"}
    assert all(isinstance(value, bool) for value in before.values())

    monitor.sample()
    after = monitor.capabilities()
    assert set(after) == {"ram", "cpu", "logical_cpu"}
    assert all(isinstance(value, bool) for value in after.values())


def test_monitor_age_and_reset() -> None:
    monitor = ResourceMonitor(sample_seconds=0.5, max_age_seconds=5.0)

    assert monitor.age() is None

    monitor.sample()
    age = monitor.age()
    assert age is not None and age >= 0.0

    monitor.reset()
    assert monitor.age() is None
    assert isinstance(monitor.sample(), ResourceSnapshot)


def test_monitor_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        ResourceMonitor(sample_seconds=0.0)
    with pytest.raises(ValueError):
        ResourceMonitor(max_age_seconds=-1.0)
    with pytest.raises(TypeError):
        ResourceMonitor(clock=object())  # type: ignore[arg-type]


def test_monitor_unavailable_metric_is_conservative_and_marked() -> None:
    monitor = BrokenRamMonitor(sample_seconds=0.5, max_age_seconds=5.0)
    snapshot = monitor.sample()

    # The fallback is the documented conservative value, never a healthy guess.
    assert snapshot.ram_available_gb == CONSERVATIVE_RAM_AVAILABLE_GB == 0.0
    assert monitor.capabilities()["ram"] is False
    assert "ram" in monitor.unavailable_metrics()
    assert monitor.degraded() is True
    assert monitor.telemetry_available() is False
    # The module-level marker is explicit and falsy.
    assert UNAVAILABLE is not None
    assert bool(UNAVAILABLE) is False
    assert repr(UNAVAILABLE) == "UNAVAILABLE"


def test_monitor_unavailable_cpu_is_conservative() -> None:
    class BrokenCpuMonitor(ResourceMonitor):
        def _sample_cpu(self) -> float:
            raise OSError("CPU telemetry is not available on this test double")

    monitor = BrokenCpuMonitor(sample_seconds=0.5, max_age_seconds=5.0)
    snapshot = monitor.sample()

    assert snapshot.cpu_load_percent == CONSERVATIVE_CPU_LOAD == 100.0
    assert monitor.capabilities()["cpu"] is False
    assert monitor.telemetry_available() is False


def test_monitor_clamps_out_of_range_cpu_load() -> None:
    monitor = OutOfRangeCpuMonitor(sample_seconds=0.5, max_age_seconds=5.0)
    snapshot = monitor.sample()

    assert snapshot.cpu_load_percent == 0.0
    assert monitor.capabilities()["cpu"] is True


def test_monitor_is_thread_safe() -> None:
    clock = ManualClock()
    monitor = ResourceMonitor(
        sample_seconds=1.0, max_age_seconds=15.0, clock=clock
    )
    results: list[ResourceSnapshot] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(monitor.sample())
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(results) == 8
    assert all(isinstance(item, ResourceSnapshot) for item in results)
    # All threads observed the single cached snapshot.
    assert len({id(item) for item in results}) == 1


# --------------------------------------------------------------------------- #
# ResourceGovernor — admission
# --------------------------------------------------------------------------- #
def test_governor_admits_when_healthy() -> None:
    governor, monitor, scheduler = build_governor()

    admission = governor.admit(
        "task-a", requirements=ResourceRequirements(ram_gb=0.5, cpu_cores=0.5)
    )

    assert isinstance(admission, Admission)
    assert admission.admitted is True
    assert admission.code == "admitted"
    assert admission.degraded is False
    assert admission.slot == 0
    assert admission.task_id == "task-a"
    assert scheduler.get_task("task-a").state == TaskState.RUNNING
    assert governor.status()["active_agents"] == 1
    assert monitor.sample_calls >= 1


def test_governor_refuses_high_cpu() -> None:
    monitor = StaticMonitor(healthy_snapshot(cpu=95.0))
    governor, _monitor, scheduler = build_governor(monitor=monitor)

    admission = governor.admit("task-cpu")

    assert admission.admitted is False
    assert admission.code == "resource_exhausted"
    assert admission.slot is None
    assert "CPU" in admission.reason
    # Refusal happens before submission: the scheduler is not polluted.
    assert scheduler.status()["tasks"] == 0
    assert governor.status()["active_agents"] == 0


def test_governor_refuses_when_ram_below_reserve() -> None:
    # Task needs 0.5 GB and the configured reserve is 1.0 GB -> 1.5 GB needed.
    monitor = StaticMonitor(healthy_snapshot(ram=1.2))
    governor, _monitor, scheduler = build_governor(monitor=monitor)

    admission = governor.admit(
        "task-ram", requirements=ResourceRequirements(ram_gb=0.5)
    )

    assert admission.admitted is False
    assert admission.code == "resource_exhausted"
    assert "RAM" in admission.reason
    assert scheduler.status()["tasks"] == 0


def test_governor_honours_max_concurrent_agents() -> None:
    governor, _monitor, scheduler = build_governor(
        max_concurrent_agents=1, max_concurrent_subprocesses=1
    )

    first = governor.admit("task-1")
    second = governor.admit("task-2")

    assert first.admitted is True
    assert second.admitted is False
    assert second.code == "concurrency_limit"
    assert "Concurrency" in second.reason
    assert scheduler.status()["running"] == 1
    assert governor.status()["available_agent_slots"] == 0


def test_governor_release_frees_slot() -> None:
    governor, _monitor, scheduler = build_governor()

    first = governor.admit("task-1")
    assert first.admitted is True

    governor.release("task-1")

    assert scheduler.get_task("task-1").state == TaskState.COMPLETED
    assert governor.status()["active_agents"] == 0

    second = governor.admit("task-2")
    assert second.admitted is True
    assert second.slot == 0


def test_governor_release_unknown_id_is_idempotent() -> None:
    governor, _monitor, _scheduler = build_governor()

    governor.release("never-admitted")
    governor.release("never-admitted")

    first = governor.admit("task-1")
    governor.release("task-1")
    # A second release must not corrupt the slot accounting.
    governor.release("task-1")

    status = governor.status()
    assert status["active_agents"] == 0
    assert status["available_agent_slots"] == 2
    assert status["counters"]["release_errors"] == 0
    assert first.slot == 0


def test_governor_subprocess_slot_ceiling() -> None:
    governor, _monitor, _scheduler = build_governor(
        max_concurrent_subprocesses=2
    )

    assert governor.try_subprocess_slot() is True
    assert governor.try_subprocess_slot() is True
    assert governor.try_subprocess_slot() is False
    assert governor.status()["subprocesses"]["in_use"] == 2

    governor.release_subprocess_slot()
    assert governor.try_subprocess_slot() is True

    governor.release_subprocess_slot()
    governor.release_subprocess_slot()
    governor.release_subprocess_slot()
    assert governor.status()["subprocesses"]["in_use"] == 0


def test_governor_subprocess_slots_are_thread_safe() -> None:
    governor, _monitor, _scheduler = build_governor(
        max_concurrent_subprocesses=3
    )
    granted: list[bool] = []
    barrier = threading.Barrier(30)

    def worker() -> None:
        barrier.wait(timeout=10)
        granted.append(governor.try_subprocess_slot())

    threads = [threading.Thread(target=worker) for _ in range(30)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(granted) == 30
    assert sum(1 for item in granted if item) == 3
    assert governor.status()["subprocesses"]["in_use"] == 3


# --------------------------------------------------------------------------- #
# ResourceGovernor — telemetry and status
# --------------------------------------------------------------------------- #
def test_governor_fails_closed_without_telemetry() -> None:
    monitor = StaticMonitor(None, error=RuntimeError("telemetry backend down"))
    governor, _monitor, scheduler = build_governor(monitor=monitor)

    admission = governor.admit("task-blind")

    assert admission.admitted is False
    assert admission.code == "telemetry_unavailable"
    assert "telemetry" in admission.reason.lower()
    assert "fail_closed_without_telemetry" in admission.reason
    assert scheduler.status()["tasks"] == 0

    with pytest.raises(TelemetryUnavailableError):
        governor.require_telemetry()


def test_governor_degrades_without_telemetry_when_not_fail_closed() -> None:
    monitor = StaticMonitor(None, error=RuntimeError("telemetry backend down"))
    governor, _monitor, scheduler = build_governor(
        monitor=monitor, fail_closed_without_telemetry=False
    )

    admission = governor.admit("task-degraded")

    assert admission.admitted is True
    assert admission.code == "degraded"
    assert admission.degraded is True
    assert admission.telemetry_available is False
    assert admission.slot == 0
    # Degraded admissions bypass the scheduler because telemetry is untrusted.
    assert scheduler.status()["tasks"] == 0

    governor.release("task-degraded")
    assert governor.status()["active_agents"] == 0


def test_governor_refuses_when_metric_capability_missing() -> None:
    monitor = StaticMonitor(
        healthy_snapshot(),
        capabilities={"ram": False, "cpu": True, "logical_cpu": True},
    )
    governor, _monitor, _scheduler = build_governor(monitor=monitor)

    admission = governor.admit("task-partial")

    assert admission.admitted is False
    assert admission.code == "telemetry_unavailable"
    assert "ram" in admission.reason

    with pytest.raises(TelemetryUnavailableError):
        governor.require_telemetry()


def test_governor_require_telemetry_returns_snapshot() -> None:
    expected = healthy_snapshot(cpu=12.0, ram=6.0)
    monitor = StaticMonitor(expected)
    governor, _monitor, _scheduler = build_governor(monitor=monitor)

    snapshot = governor.require_telemetry()

    assert snapshot == expected
    assert monitor.sample_calls == 1


def test_governor_refuses_thermal_policy_without_telemetry() -> None:
    monitor = StaticMonitor(healthy_snapshot())
    governor, _monitor, _scheduler = build_governor(
        monitor=monitor, thermal_limit_c=70.0
    )

    admission = governor.admit("task-thermal")

    assert admission.admitted is False
    assert admission.code == "resource_exhausted"
    assert "temperature" in admission.reason.lower()


def test_governor_status_shape_and_never_raises() -> None:
    monitor = StaticMonitor(None, error=RuntimeError("telemetry backend down"))
    governor, _monitor, _scheduler = build_governor(monitor=monitor)

    status = governor.status()

    assert {
        "max_concurrent_agents",
        "active_agents",
        "available_agent_slots",
        "admitted_tasks",
        "subprocesses",
        "telemetry",
        "scheduler",
        "degraded",
        "counters",
    } <= set(status)
    assert status["max_concurrent_agents"] == 2
    assert status["subprocesses"]["max"] == 2
    assert "capabilities" in status["telemetry"]
    assert "running" in status["scheduler"]


def test_governor_status_reports_degraded_monitor() -> None:
    monitor = BrokenRamMonitor(sample_seconds=0.5, max_age_seconds=5.0)
    config = ResourceConfig()
    scheduler = ResourceScheduler(
        max_concurrent=config.max_concurrent_agents,
        max_cpu_load_percent=config.max_cpu_load_percent,
        minimum_free_ram_gb=config.min_free_ram_gb,
    )
    governor = ResourceGovernor(config, monitor=monitor, scheduler=scheduler)

    admission = governor.admit("task-degraded-status")
    assert admission.admitted is False
    assert admission.code == "telemetry_unavailable"

    status = governor.status()
    assert status["degraded"] is True
    assert status["telemetry"]["capabilities"]["ram"] is False
    assert status["telemetry"]["available"] is False


# --------------------------------------------------------------------------- #
# ResourceGovernor — strict API and idempotency
# --------------------------------------------------------------------------- #
def test_governor_admit_strict_raises_concurrency_error() -> None:
    governor, _monitor, _scheduler = build_governor(max_concurrent_agents=1)

    assert governor.admit("task-1").admitted is True
    with pytest.raises(ConcurrencyLimitError):
        governor.admit_strict("task-2")


def test_governor_admit_strict_raises_resource_error() -> None:
    monitor = StaticMonitor(healthy_snapshot(cpu=99.0))
    governor, _monitor, _scheduler = build_governor(monitor=monitor)

    with pytest.raises(ResourceExhaustedError):
        governor.admit_strict("task-hot")


def test_governor_duplicate_admission_is_idempotent() -> None:
    governor, _monitor, scheduler = build_governor()

    first = governor.admit("task-1")
    second = governor.admit("task-1")

    assert first.admitted is True
    assert second.admitted is True
    assert second.slot == first.slot
    assert second.reason == "Task is already admitted."
    assert scheduler.status()["tasks"] == 1


def test_governor_audits_admission_decisions(tmp_path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    monitor = StaticMonitor(healthy_snapshot())
    config = ResourceConfig()
    scheduler = ResourceScheduler(
        max_concurrent=config.max_concurrent_agents,
        max_cpu_load_percent=config.max_cpu_load_percent,
        minimum_free_ram_gb=config.min_free_ram_gb,
    )
    governor = ResourceGovernor(
        config, monitor=monitor, scheduler=scheduler, audit=audit
    )

    assert governor.admit("task-audit").admitted is True
    governor.release("task-audit")

    records = list(audit.records())
    actions = [record["action"] for record in records]
    assert "resource.admission" in actions
    assert "resource.release" in actions
    assert audit.verify_chain()["valid"] is True


# --------------------------------------------------------------------------- #
# Workspace package contract
# --------------------------------------------------------------------------- #
def test_workspace_package_exports_backup_only() -> None:
    import matlab_os.phase9.workspace as workspace_package

    assert workspace_package.__all__ == ["BackupRecord", "BackupStore"]
    assert workspace_package.BackupStore is BackupStore
    assert workspace_package.BackupRecord is BackupRecord
    with pytest.raises(AttributeError):
        getattr(workspace_package, "WorkspaceManager")

    # The package must not import ``operations`` eagerly.  The runtime imports
    # it transitively, so this has to be proven in a fresh interpreter rather
    # than by inspecting this process's ``sys.modules``.
    import subprocess
    from pathlib import Path

    package_dir = Path(workspace_package.__file__).resolve().parent
    staging_root = package_dir.parent
    project_root = staging_root.parent
    program = (
        "import sys;"
        f"sys.path[:0]=[{str(project_root)!r}, {str(staging_root)!r}];"
        "import matlab_os.phase9.workspace;"
        "print('matlab_os.phase9.workspace.operations' in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == "False"


# --------------------------------------------------------------------------- #
# BackupStore — create / list / manifest
# --------------------------------------------------------------------------- #
def test_backup_create_records_and_lists(guard: PathGuard, store: BackupStore) -> None:
    guard.atomic_write_text("notes/a.txt", "alpha")

    record = store.create("notes/a.txt", reason="before edit")

    assert isinstance(record, BackupRecord)
    assert record.original_relative == "notes/a.txt"
    assert record.backup_relative == (
        f"{store.directory}/{record.backup_id}/notes/a.txt"
    )
    assert record.backup_relative.startswith(
        f"{store.directory}/{record.backup_id}/"
    )
    assert record.reason == "before edit"
    assert record.size == len("alpha".encode("utf-8"))
    assert len(record.sha256) == 64
    assert store.directory == DEFAULT_DIRECTORY == ".phase9_backups"
    assert store.manifest_relative == f"{DEFAULT_DIRECTORY}/{MANIFEST_NAME}"

    # The stored copy is byte-identical to the source.
    assert guard.read_text(record.backup_relative)[0] == "alpha"
    assert guard.read_text("notes/a.txt")[0] == "alpha"

    records = store.list()
    assert [item.backup_id for item in records] == [record.backup_id]

    manifest = guard.read_text(store.manifest_relative)[0]
    assert manifest.endswith("\n")
    entries = [json.loads(line) for line in manifest.splitlines() if line.strip()]
    assert len(entries) == 1
    assert entries[0]["backup_id"] == record.backup_id
    assert entries[0]["sha256"] == record.sha256


def test_backup_manifest_appends_one_line_per_backup(
    guard: PathGuard, store: BackupStore
) -> None:
    guard.atomic_write_text("value.txt", "v1")
    first = store.create("value.txt")
    guard.atomic_write_text("value.txt", "v2")
    second = store.create("value.txt")
    guard.atomic_write_text("value.txt", "v3")
    third = store.create("value.txt")

    manifest = guard.read_text(store.manifest_relative)[0]
    lines = [line for line in manifest.splitlines() if line.strip()]
    assert len(lines) == 3
    ids = [json.loads(line)["backup_id"] for line in lines]
    assert ids == [first.backup_id, second.backup_id, third.backup_id]

    records = store.list()
    assert records[0].backup_id == third.backup_id
    assert records[-1].backup_id == first.backup_id


def test_backup_custom_directory(guard: PathGuard) -> None:
    store = BackupStore(guard, directory="backups/vault", retention=5)
    guard.atomic_write_text("doc.txt", "payload")

    record = store.create("doc.txt")

    assert store.directory == "backups/vault"
    assert store.manifest_relative == "backups/vault/manifest.jsonl"
    assert guard.read_text(record.backup_relative)[0] == "payload"
    assert guard.read_text("backups/vault/manifest.jsonl")[0].strip()


def test_backup_constructor_validation(guard: PathGuard) -> None:
    with pytest.raises(TypeError):
        BackupStore(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        BackupStore(guard, retention=-1)
    with pytest.raises(ValueError):
        BackupStore(guard, directory="")


def test_backup_create_requires_existing_regular_file(
    guard: PathGuard, store: BackupStore
) -> None:
    with pytest.raises(WorkspaceError):
        store.create("missing.txt")

    guard.ensure_directory("folder")
    with pytest.raises(WorkspaceError):
        store.create("folder")


# --------------------------------------------------------------------------- #
# BackupStore — verify / restore
# --------------------------------------------------------------------------- #
def test_backup_restore_restores_exact_bytes(
    guard: PathGuard, store: BackupStore
) -> None:
    original = b"\x00\x01phase9\r\nbinary\xff"
    guard.atomic_write_bytes("data/blob.bin", original)
    record = store.create("data/blob.bin")

    guard.atomic_write_bytes("data/blob.bin", b"mutated")

    restored = store.restore(record.backup_id)

    assert isinstance(restored, ResolvedPath)
    assert restored.relative == "data/blob.bin"
    assert restored.is_file is True
    assert guard.read_bytes("data/blob.bin")[0] == original


def test_backup_restore_recreates_deleted_file(
    guard: PathGuard, store: BackupStore
) -> None:
    guard.atomic_write_text("gone.txt", "restore me")
    record = store.create("gone.txt")

    (guard.root / "gone.txt").unlink()
    assert not (guard.root / "gone.txt").exists()

    restored = store.restore(record.backup_id)

    assert restored.is_file is True
    assert guard.read_text("gone.txt")[0] == "restore me"


def test_backup_verify_detects_tampering(
    guard: PathGuard, store: BackupStore
) -> None:
    guard.atomic_write_text("doc.txt", "trusted")
    record = store.create("doc.txt")

    assert store.verify(record.backup_id) is True

    guard.atomic_write_text(record.backup_relative, "tampered")

    assert store.verify(record.backup_id) is False
    with pytest.raises(IntegrityError):
        store.restore(record.backup_id)


def test_backup_verify_returns_false_when_copy_missing(
    guard: PathGuard, store: BackupStore
) -> None:
    guard.atomic_write_text("doc.txt", "trusted")
    record = store.create("doc.txt")

    # Remove the stored copy behind the store's back.
    (guard.root / record.backup_relative).unlink()

    assert store.verify(record.backup_id) is False
    with pytest.raises(BackupError):
        store.restore(record.backup_id)


def test_backup_unknown_id_raises(store: BackupStore) -> None:
    assert store.list() == []

    with pytest.raises(BackupError):
        store.restore("missing-backup")
    with pytest.raises(BackupError):
        store.verify("missing-backup")


def test_backup_restore_is_bound_to_the_recorded_hash(
    guard: PathGuard, store: BackupStore
) -> None:
    guard.atomic_write_text("doc.txt", "content")
    record = store.create("doc.txt")

    # Make the stored bytes differ from the recorded hash.
    guard.atomic_write_bytes(record.backup_relative, b"different")

    with pytest.raises(IntegrityError):
        store.restore(record.backup_id)
    # The original file must not be overwritten by a failed restore.
    guard.atomic_write_text("doc.txt", "still here")
    assert store.verify(record.backup_id) is False


# --------------------------------------------------------------------------- #
# BackupStore — security boundary
# --------------------------------------------------------------------------- #
def test_backup_traversal_rejected_by_path_guard(
    guard: PathGuard, store: BackupStore
) -> None:
    with pytest.raises(PathSecurityError):
        store.create("../escape.txt", reason="traversal")
    with pytest.raises(PathSecurityError):
        store.create("..\\escape.txt", reason="traversal")
    with pytest.raises(PathSecurityError):
        store.create("notes/../../escape.txt", reason="traversal")

    # Nothing escaped the workspace.
    assert not (guard.root.parent / "escape.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="drive-qualified paths are Windows-specific")
def test_backup_absolute_path_rejected_by_path_guard(
    store: BackupStore,
) -> None:
    with pytest.raises(PathSecurityError):
        store.create("C:\\Windows\\win.ini")


# --------------------------------------------------------------------------- #
# BackupStore — retention
# --------------------------------------------------------------------------- #
def test_backup_pruning_keeps_newest(guard: PathGuard) -> None:
    clock = ManualClock()
    store = BackupStore(guard, retention=3, clock=clock)

    backup_ids: list[str] = []
    for index in range(5):
        guard.atomic_write_text("value.txt", f"version-{index}")
        backup_ids.append(store.create("value.txt").backup_id)
        clock.advance(1.0)

    records = store.list()
    assert len(records) == 3
    assert records[0].backup_id == backup_ids[-1]
    assert {record.backup_id for record in records} == set(backup_ids[-3:])

    # The newest backup survives and is still valid.
    assert store.verify(backup_ids[-1]) is True
    assert (guard.root / store.directory / backup_ids[-1]).is_dir()

    # The oldest backups are gone from disk and from the manifest.
    for pruned_id in backup_ids[:2]:
        assert not (guard.root / store.directory / pruned_id).exists()
        with pytest.raises(BackupError):
            store.restore(pruned_id)

    # A second prune has nothing left to do.
    assert store.prune() == 0


def test_backup_retention_zero_keeps_newest(guard: PathGuard) -> None:
    store = BackupStore(guard, retention=0)

    guard.atomic_write_text("value.txt", "first")
    first = store.create("value.txt")
    guard.atomic_write_text("value.txt", "second")
    second = store.create("value.txt")

    records = store.list()
    assert len(records) == 1
    assert records[0].backup_id == second.backup_id
    assert store.verify(second.backup_id) is True
    with pytest.raises(BackupError):
        store.restore(first.backup_id)


# --------------------------------------------------------------------------- #
# BackupStore — audit, corruption, concurrency
# --------------------------------------------------------------------------- #
def test_backup_audits_workspace_backup(guard: PathGuard, tmp_path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    store = BackupStore(guard, audit=audit)

    guard.atomic_write_text("doc.txt", "audited")
    record = store.create("doc.txt", reason="audit test")

    records = list(audit.records())
    backup_records = [
        item for item in records if item["action"] == "workspace.backup"
    ]
    assert len(backup_records) == 1
    assert backup_records[0]["outcome"] == "success"
    assert backup_records[0]["details"]["backup_id"] == record.backup_id
    assert backup_records[0]["details"]["sha256"] == record.sha256
    assert audit.verify_chain()["valid"] is True


def test_backup_corrupt_manifest_raises(
    guard: PathGuard, store: BackupStore
) -> None:
    guard.atomic_write_text("doc.txt", "payload")
    store.create("doc.txt")

    guard.atomic_write_text(store.manifest_relative, "this is not json\n")

    with pytest.raises(BackupError):
        store.list()


def test_backup_is_thread_safe(guard: PathGuard) -> None:
    store = BackupStore(guard, retention=100)
    guard.atomic_write_text("shared.txt", "threaded payload")
    errors: list[BaseException] = []
    barrier = threading.Barrier(10)

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            store.create("shared.txt", reason="thread")
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    records = store.list()
    assert len(records) == 10
    assert len({record.backup_id for record in records}) == 10
    assert all(store.verify(record.backup_id) for record in records)
    manifest = guard.read_text(store.manifest_relative)[0]
    assert len([line for line in manifest.splitlines() if line.strip()]) == 10
