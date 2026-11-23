"""Stdlib-only host telemetry for Phase 9 (staging only).

The monitor samples CPU load and available RAM without ``psutil`` and converts
them into the reused
:class:`matlab_os.intelligence.resource_scheduler.ResourceSnapshot` dataclass so
the existing :class:`~matlab_os.intelligence.resource_scheduler.ResourceScheduler`
keeps working unchanged.

Platform strategy
-----------------
* **Windows** – ``GlobalMemoryStatusEx`` for physical memory and
  ``GetSystemTimes`` deltas (idle / kernel / user) for CPU load, both through
  ``ctypes``.  ``GetSystemTimes`` reports cumulative counters, so the very first
  snapshot is measured over a short bounded calibration window instead of
  fabricating a zero-load reading.
* **POSIX** – ``/proc/meminfo`` (``MemAvailable`` / ``MemTotal``) for memory and
  ``os.getloadavg()`` normalised by ``os.cpu_count()`` for CPU load.  When
  ``/proc/meminfo`` is absent (for example on macOS) RAM is reported as
  unavailable instead of being guessed.

Missing telemetry
-----------------
A metric that cannot be sampled is **never fabricated**.  The corresponding
entry of :meth:`ResourceMonitor.capabilities` becomes ``False``, the metric is
listed by :meth:`ResourceMonitor.unavailable_metrics`, and the snapshot carries
a deliberately *conservative* substitute:

* CPU load      -> :data:`CONSERVATIVE_CPU_LOAD` (100.0 %, i.e. "busy")
* available RAM -> :data:`CONSERVATIVE_RAM_AVAILABLE_GB` (0.0 GB, i.e. "none")
* logical CPUs  -> :data:`CONSERVATIVE_LOGICAL_CPU_COUNT` (1)

Callers that must not act on degraded data use
:meth:`ResourceMonitor.telemetry_available` or
:meth:`ResourceMonitor.unavailable_metrics`; :data:`UNAVAILABLE` is also
exported as an explicit, falsy marker for callers that prefer marker values.

Rules honoured
--------------
* ``stdlib`` only (``ctypes`` is part of the standard library);
* no OS sampling happens at import time;
* every OS call is wrapped in ``try`` / ``except`` so the module imports and
  works on non-Windows hosts;
* sampling is cached for ``max_age_seconds`` so repeated calls do not hammer
  the operating system;
* the clock is injectable (``clock=time.time``) for deterministic tests;
* mutable cache state is guarded by an ``RLock``.
"""

from __future__ import annotations

import ctypes
import math
import os
import threading
import time
from typing import Any, Callable, Optional

from matlab_os.intelligence.resource_scheduler import ResourceSnapshot

_GB_IN_BYTES = 1024.0 ** 3

#: Conservative CPU load reported when the metric cannot be sampled.
CONSERVATIVE_CPU_LOAD = 100.0
#: Conservative available-RAM value reported when the metric cannot be sampled.
CONSERVATIVE_RAM_AVAILABLE_GB = 0.0
#: Conservative logical-CPU count reported when the count cannot be sampled.
CONSERVATIVE_LOGICAL_CPU_COUNT = 1

#: Capability keys reported by :meth:`ResourceMonitor.capabilities`.
CAPABILITY_KEYS = ("ram", "cpu", "logical_cpu")

#: One-off measurement window used to turn cumulative Windows CPU counters into
#: a real load value on the very first sample.
_FIRST_SAMPLE_CPU_WINDOW_SECONDS = 0.1


class _UnavailableMarker:
    """Explicit, falsy marker for a metric that could not be sampled."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "UNAVAILABLE"

    def __bool__(self) -> bool:
        return False


#: Module-level marker documented for callers that prefer explicit sentinels.
UNAVAILABLE = _UnavailableMarker()


# --------------------------------------------------------------------------- #
# Windows ctypes structures (defined only; no native call at import time)
# --------------------------------------------------------------------------- #
class _MemoryStatusEx(ctypes.Structure):
    """Mirror of the Win32 ``MEMORYSTATUSEX`` structure."""

    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _FileTime(ctypes.Structure):
    """Mirror of the Win32 ``FILETIME`` structure."""

    _fields_ = [
        ("dwLowDateTime", ctypes.c_ulong),
        ("dwHighDateTime", ctypes.c_ulong),
    ]


def _filetime_value(value: _FileTime) -> int:
    """Return a 64-bit tick count from a ``FILETIME`` structure."""
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


_WINDOWS_API: Optional[dict[str, Any]] = None
_WINDOWS_API_LOCK = threading.Lock()


def _windows_api() -> dict[str, Any]:
    """Load and configure the Win32 entry points exactly once.

    The call is deferred until the first sample so that importing this module
    never touches the operating system.
    """
    global _WINDOWS_API

    if os.name != "nt":
        raise OSError("Windows telemetry API is unavailable on this platform.")

    with _WINDOWS_API_LOCK:
        if _WINDOWS_API is None:
            windll = getattr(ctypes, "WinDLL", None)
            if windll is None:  # pragma: no cover - defensive
                raise OSError("ctypes.WinDLL is unavailable on this host.")
            kernel32 = windll("kernel32", use_last_error=True)

            memory_status = kernel32.GlobalMemoryStatusEx
            memory_status.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
            memory_status.restype = ctypes.c_int

            system_times = kernel32.GetSystemTimes
            system_times.argtypes = [
                ctypes.POINTER(_FileTime),
                ctypes.POINTER(_FileTime),
                ctypes.POINTER(_FileTime),
            ]
            system_times.restype = ctypes.c_int

            _WINDOWS_API = {
                "memory_status": memory_status,
                "system_times": system_times,
            }
        return _WINDOWS_API


def _read_system_times() -> tuple[int, int, int]:
    """Read cumulative ``(idle, kernel, user)`` CPU tick counters."""
    api = _windows_api()
    idle = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    ok = api["system_times"](
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    )
    if not ok:
        raise OSError("GetSystemTimes failed.")
    return _filetime_value(idle), _filetime_value(kernel), _filetime_value(user)


def _windows_memory_gb() -> tuple[float, float]:
    """Return ``(available_gb, total_gb)`` of physical memory."""
    api = _windows_api()
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not api["memory_status"](ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed.")
    total = float(status.ullTotalPhys) / _GB_IN_BYTES
    available = float(status.ullAvailPhys) / _GB_IN_BYTES
    if total <= 0.0:
        raise OSError("GlobalMemoryStatusEx reported no physical memory.")
    return available, total


def _posix_memory_gb() -> tuple[float, float]:
    """Return ``(available_gb, total_gb)`` from ``/proc/meminfo``.

    ``MemAvailable`` is required; a kernel that does not report it leaves RAM
    unavailable rather than inventing a free-memory figure.
    """
    values: dict[str, float] = {}
    with open("/proc/meminfo", "r", encoding="ascii", errors="replace") as handle:
        for line in handle:
            key, separator, rest = line.partition(":")
            if not separator:
                continue
            fields = rest.split()
            if not fields:
                continue
            try:
                amount = float(fields[0])
            except ValueError:
                continue
            values[key.strip()] = amount

    available_kb = values.get("MemAvailable")
    total_kb = values.get("MemTotal")
    if available_kb is None:
        raise OSError("MemAvailable is not reported by /proc/meminfo.")
    if total_kb is None or total_kb <= 0.0:
        raise OSError("MemTotal is not reported by /proc/meminfo.")
    return (
        available_kb * 1024.0 / _GB_IN_BYTES,
        total_kb * 1024.0 / _GB_IN_BYTES,
    )


class ResourceMonitor:
    """Cache stdlib CPU/RAM telemetry as a reused ``ResourceSnapshot``.

    Parameters
    ----------
    sample_seconds:
        Intended sampling period in seconds (metadata for callers; the cache is
        keyed by ``max_age_seconds``).  Must be positive.
    max_age_seconds:
        Maximum age of a cached snapshot before :meth:`sample` resamples.
        ``0`` disables caching.
    clock:
        Injectable clock used for timestamps and cache-age computation.
    """

    def __init__(
        self,
        *,
        sample_seconds: float = 1.0,
        max_age_seconds: float = 15.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        try:
            sample = float(sample_seconds)
            max_age = float(max_age_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "sample_seconds and max_age_seconds must be numbers."
            ) from exc
        if not math.isfinite(sample) or sample <= 0.0:
            raise ValueError("sample_seconds must be a positive number.")
        if not math.isfinite(max_age) or max_age < 0.0:
            raise ValueError("max_age_seconds cannot be negative.")
        if not callable(clock):
            raise TypeError("clock must be callable.")

        self.sample_seconds = sample
        self.max_age_seconds = max_age
        self._clock = clock

        self._lock = threading.RLock()
        self._snapshot: Optional[ResourceSnapshot] = None
        self._capabilities: Optional[dict[str, bool]] = None
        self._unavailable: tuple[str, ...] = ()
        self._sampled_at: Optional[float] = None

        # Cumulative CPU counters used to compute a Windows delta.
        self._system_times_baseline: Optional[tuple[int, int, int]] = None

    # -- public API -------------------------------------------------------- #
    def sample(self, force: bool = False) -> ResourceSnapshot:
        """Return a snapshot, resampling at most once per ``max_age_seconds``.

        Missing metrics are replaced by the documented conservative values and
        reported through :meth:`capabilities` / :meth:`unavailable_metrics`.
        """
        with self._lock:
            now = self._now()
            if not force and self._snapshot is not None:
                age = now - self._sampled_at  # type: ignore[operator]
                if 0.0 <= age <= self.max_age_seconds:
                    return self._snapshot

            snapshot, capabilities, unavailable = self._collect()
            self._snapshot = snapshot
            self._capabilities = capabilities
            self._unavailable = unavailable
            self._sampled_at = now
            return snapshot

    def age(self) -> Optional[float]:
        """Return the age in seconds of the cached snapshot, or ``None``."""
        with self._lock:
            if self._snapshot is None or self._sampled_at is None:
                return None
            return max(0.0, self._now() - self._sampled_at)

    def capabilities(self) -> dict[str, bool]:
        """Return per-metric availability of the most recent sample.

        Before the first sample this reports *platform support* (whether the
        sampler can work at all), never a fabricated health reading.
        """
        with self._lock:
            if self._capabilities is not None:
                return dict(self._capabilities)
            return self._platform_support()

    def unavailable_metrics(self) -> tuple[str, ...]:
        """Return the metrics the most recent sample could not read."""
        with self._lock:
            if self._snapshot is None:
                return tuple(
                    key
                    for key, supported in self._platform_support().items()
                    if not supported
                )
            return tuple(self._unavailable)

    def degraded(self) -> bool:
        """Return ``True`` when the most recent sample lacked a metric."""
        return bool(self.unavailable_metrics())

    def telemetry_available(self) -> bool:
        """Return ``True`` when a fresh, complete snapshot is cached."""
        with self._lock:
            if self._snapshot is None:
                return False
            if self._unavailable:
                return False
            if self._capabilities is not None and not all(
                self._capabilities.values()
            ):
                return False
            age = self.age()
            return age is not None and age <= self.max_age_seconds

    def reset(self) -> None:
        """Drop the cached telemetry so the next call resamples.

        The cumulative Windows CPU counters are intentionally retained: they
        are a sampling baseline, not client-facing cached telemetry, and
        keeping them avoids a second calibration window after a reset.
        """
        with self._lock:
            self._snapshot = None
            self._capabilities = None
            self._unavailable = ()
            self._sampled_at = None

    # -- sampling helpers -------------------------------------------------- #
    def _now(self) -> float:
        return float(self._clock())

    def _collect(self) -> tuple[ResourceSnapshot, dict[str, bool], tuple[str, ...]]:
        capabilities: dict[str, bool] = {key: True for key in CAPABILITY_KEYS}
        unavailable: list[str] = []

        try:
            ram_available, _ram_total = self._sample_ram()
            if not math.isfinite(ram_available) or ram_available < 0.0:
                raise ValueError("sampled RAM availability is invalid.")
        except Exception:  # noqa: BLE001 - telemetry failure is reported, not raised
            capabilities["ram"] = False
            unavailable.append("ram")
            ram_available = CONSERVATIVE_RAM_AVAILABLE_GB

        try:
            cpu_load = float(self._sample_cpu())
            if not math.isfinite(cpu_load):
                raise ValueError("sampled CPU load is invalid.")
            cpu_load = min(100.0, max(0.0, cpu_load))
        except Exception:  # noqa: BLE001
            capabilities["cpu"] = False
            unavailable.append("cpu")
            cpu_load = CONSERVATIVE_CPU_LOAD

        try:
            cpu_count = int(self._sample_logical_cpu())
            if cpu_count < 1:
                raise ValueError("sampled logical CPU count is invalid.")
        except Exception:  # noqa: BLE001
            capabilities["logical_cpu"] = False
            unavailable.append("logical_cpu")
            cpu_count = CONSERVATIVE_LOGICAL_CPU_COUNT

        snapshot = ResourceSnapshot(
            cpu_load_percent=float(cpu_load),
            ram_available_gb=float(ram_available),
            logical_cpu_count=int(cpu_count),
            timestamp=self._now(),
        )
        return snapshot, capabilities, tuple(unavailable)

    def _sample_ram(self) -> tuple[float, float]:
        if os.name == "nt":
            return _windows_memory_gb()
        return _posix_memory_gb()

    def _sample_cpu(self) -> float:
        if os.name == "nt":
            return self._sample_cpu_windows()
        return self._sample_cpu_posix()

    def _sample_cpu_windows(self) -> float:
        """Compute CPU load from cumulative ``GetSystemTimes`` deltas."""
        latest = _read_system_times()
        baseline = self._system_times_baseline
        if baseline is None:
            # A delta needs two readings.  Calibrate once over a short bounded
            # window so the first snapshot is a real measurement.
            time.sleep(_FIRST_SAMPLE_CPU_WINDOW_SECONDS)
            baseline = latest
            latest = _read_system_times()

        idle_delta = latest[0] - baseline[0]
        kernel_delta = latest[1] - baseline[1]
        user_delta = latest[2] - baseline[2]
        # Kernel time includes idle time, so busy = kernel + user - idle.
        total = kernel_delta + user_delta
        if total <= 0:
            raise OSError("GetSystemTimes reported no elapsed CPU time.")
        busy = total - idle_delta
        self._system_times_baseline = latest
        return min(100.0, max(0.0, busy / total * 100.0))

    def _sample_cpu_posix(self) -> float:
        load_average = getattr(os, "getloadavg", None)
        if load_average is None:
            raise OSError("os.getloadavg is unavailable on this platform.")
        values = load_average()
        if not values:
            raise OSError("os.getloadavg returned no values.")
        count = self._sample_logical_cpu()
        return min(100.0, max(0.0, float(values[0]) / count * 100.0))

    def _sample_logical_cpu(self) -> int:
        try:
            count = os.cpu_count()
        except Exception as exc:  # pragma: no cover - defensive
            raise OSError("Logical CPU count is unavailable.") from exc
        if not count:
            raise OSError("Logical CPU count is unavailable.")
        return int(count)

    def _platform_support(self) -> dict[str, bool]:
        """Return static platform capability, without performing a sample."""
        logical_cpu = False
        try:
            logical_cpu = bool(os.cpu_count())
        except Exception:  # pragma: no cover - defensive
            logical_cpu = False

        if os.name == "nt":
            supported = False
            try:
                _windows_api()
                supported = True
            except Exception:  # noqa: BLE001 - capability probe must not raise
                supported = False
            return {
                "ram": supported,
                "cpu": supported,
                "logical_cpu": logical_cpu,
            }

        return {
            "ram": os.path.isfile("/proc/meminfo"),
            "cpu": getattr(os, "getloadavg", None) is not None,
            "logical_cpu": logical_cpu,
        }


__all__ = [
    "CAPABILITY_KEYS",
    "CONSERVATIVE_CPU_LOAD",
    "CONSERVATIVE_LOGICAL_CPU_COUNT",
    "CONSERVATIVE_RAM_AVAILABLE_GB",
    "ResourceMonitor",
    "UNAVAILABLE",
]
