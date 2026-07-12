from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import math
import os
from pathlib import Path
import time
from threading import Lock
from io import BufferedReader
from typing import Final
import tracemalloc
from unittest.mock import patch

import core.provenance_capture as capture
from core.provenance_lifecycle_types import ObservationResult

MIB: Final = 1024 * 1024
SLO_BUDGETS_MS: Final = {
    1_000: {"fast_path": 200, "cold_start": 1_000, "post_tool": 200, "stop": 1_000},
    10_000: {"fast_path": 1_000, "cold_start": 6_000, "post_tool": 1_000, "stop": 6_000},
}


@dataclass(frozen=True, slots=True)
class PhaseMeasurement:
    elapsed_ns: int
    content_read_bytes: int
    hash_calls: int
    stat_count: int
    tracemalloc_peak_bytes: int
    rss_peak_bytes: int
    incomplete: bool
    full_scan: bool


@dataclass(frozen=True, slots=True)
class PhaseStats:
    sample_count: int
    p50_ns: int
    p95_ns: int
    p99_ns: int
    max_ns: int
    content_read_bytes: int
    hash_calls: int
    stat_count: int
    tracemalloc_peak_bytes: int
    rss_peak_bytes: int
    incomplete_count: int
    full_scan_count: int


@dataclass(frozen=True, slots=True)
class SloResult:
    passed: bool
    failures: tuple[str, ...]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def measure(action: Callable[[], ObservationResult]) -> tuple[ObservationResult, PhaseMeasurement]:
    read_bytes = 0
    hash_calls = 0
    counter_lock = Lock()
    original: Callable[[BufferedReader], str] = capture._digest_stream

    def counting_digest(handle: BufferedReader) -> str:
        nonlocal read_bytes, hash_calls
        before = handle.tell()
        digest = original(handle)
        with counter_lock:
            read_bytes += handle.tell() - before
            hash_calls += 1
        return digest

    rss_before = _current_rss_bytes()
    with patch.object(capture, "_digest_stream", counting_digest):
        started = time.perf_counter_ns()
        result = action()
        elapsed = time.perf_counter_ns() - started
    entries = len(result.snapshot.entries) if result.snapshot is not None else 0
    return result, PhaseMeasurement(
        elapsed,
        read_bytes,
        hash_calls,
        entries + (hash_calls * 3),
        0,
        max(0, _peak_rss_bytes() - rss_before),
        result.incomplete,
        result.full_scan,
    )


def measure_memory(action: Callable[[], ObservationResult]) -> tuple[ObservationResult, int, int]:
    rss_before = _current_rss_bytes()
    tracemalloc.start()
    try:
        result = action()
        _, traced_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, traced_peak, max(0, _peak_rss_bytes() - rss_before)


def summarize_phase(measurements: tuple[PhaseMeasurement, ...]) -> PhaseStats:
    durations = tuple(sorted(item.elapsed_ns for item in measurements))
    return PhaseStats(
        len(measurements),
        _percentile(durations, 0.50),
        _percentile(durations, 0.95),
        _percentile(durations, 0.99),
        max(durations, default=0),
        sum(item.content_read_bytes for item in measurements),
        sum(item.hash_calls for item in measurements),
        sum(item.stat_count for item in measurements),
        max((item.tracemalloc_peak_bytes for item in measurements), default=0),
        max((item.rss_peak_bytes for item in measurements), default=0),
        sum(item.incomplete for item in measurements),
        sum(item.full_scan for item in measurements),
    )


def evaluate_slo(phases: Mapping[str, PhaseStats], rss_peak_bytes: int, file_count: int = 1_000) -> SloResult:
    budgets = budgets_for_scale(file_count)
    failures: list[str] = []
    for name, budget_ms in budgets.items():
        stats = phases.get(name)
        if stats is None:
            failures.append(f"missing_{name}")
            continue
        if stats.p95_ns > budget_ms * 1_000_000:
            failures.append(f"{name}_p95")
        if stats.incomplete_count:
            failures.append(f"{name}_incomplete")
    fast = phases.get("fast_path")
    if fast is not None and fast.content_read_bytes:
        failures.append("fast_path_content_reads")
    if fast is not None and fast.full_scan_count:
        failures.append("fast_path_full_fallback")
    if rss_peak_bytes > 80 * MIB:
        failures.append("rss_peak")
    return SloResult(not failures, tuple(failures))


def budgets_for_scale(file_count: int) -> dict[str, int]:
    try:
        return SLO_BUDGETS_MS[file_count]
    except KeyError as exc:
        raise ValueError(f"no release SLO for {file_count} files") from exc


def _percentile(values: tuple[int, ...], fraction: float) -> int:
    if not values:
        return 0
    return values[math.ceil(len(values) * fraction) - 1]


def _peak_rss_bytes() -> int:
    if os.name == "nt":
        return _windows_rss_bytes()[1]
    try:
        import resource
    except ImportError:
        return 0
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if os.uname().sysname == "Darwin" else peak * 1024


def _current_rss_bytes() -> int:
    if os.name == "nt":
        return _windows_rss_bytes()[0]
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        resident_pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
    except (AttributeError, IndexError, OSError, ValueError):
        return _peak_rss_bytes()
    return page_size * resident_pages


def _windows_rss_bytes() -> tuple[int, int]:
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("Kernel32.dll")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    process = kernel.GetCurrentProcess()
    psapi = ctypes.WinDLL("Psapi.dll")
    info = psapi.GetProcessMemoryInfo
    info.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessMemoryCounters), wintypes.DWORD)
    info.restype = wintypes.BOOL
    passed = info(process, ctypes.byref(counters), counters.cb)
    if not passed:
        return 0, 0
    return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)
