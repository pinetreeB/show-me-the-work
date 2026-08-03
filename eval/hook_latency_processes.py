from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter_ns
from typing import Final, TypeAlias


ROOT: Final = Path(__file__).resolve().parents[1]
BENCH: Final = Path(__file__).with_name("hook_latency_bench.py")
ADAPTER: Final = ROOT / "adapters" / "codex_cli" / "pre_tool_use.py"
LAUNCHED_NS_ENV: Final = "SMTW_HOOK_BENCH_LAUNCHED_NS"

PhaseSample: TypeAlias = dict[str, float]
ActualProcess: TypeAlias = tuple[subprocess.Popen[str], int, str]


def _elapsed_ms(started_ns: int, ended_ns: int) -> float:
    return (ended_ns - started_ns) / 1_000_000


def _environment(launched_ns: int) -> dict[str, str]:
    return {
        **os.environ,
        LAUNCHED_NS_ENV: str(launched_ns),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(ROOT),
    }


def _start_phase_worker(root: Path) -> subprocess.Popen[str]:
    launched_ns = perf_counter_ns()
    return subprocess.Popen(
        [sys.executable, str(BENCH), "--worker-root", str(root)],
        cwd=ROOT,
        env=_environment(launched_ns),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def _finish_phase_worker(
    process: subprocess.Popen[str],
    phases: tuple[str, ...],
) -> PhaseSample:
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise ChildProcessError(stderr.strip())
    raw = json.loads(stdout)
    if not isinstance(raw, dict):
        raise ChildProcessError("phase worker did not emit an object")
    sample: PhaseSample = {}
    for phase in phases:
        value = raw.get(phase)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ChildProcessError(f"worker phase is not numeric: {phase}")
        sample[phase] = float(value)
    return sample


def run_phase_group(
    root: Path,
    concurrency: int,
    phases: tuple[str, ...],
) -> tuple[list[PhaseSample], float]:
    started_ns = perf_counter_ns()
    processes = [_start_phase_worker(root) for _ in range(concurrency)]
    samples = [_finish_phase_worker(process, phases) for process in processes]
    return samples, _elapsed_ms(started_ns, perf_counter_ns())


def _start_actual_adapter(root: Path, index: int) -> ActualProcess:
    launched_ns = perf_counter_ns()
    identity = f"hook-bench-{os.getpid()}-{launched_ns}-{index}"
    payload = json.dumps(
        {
            "cwd": str(root),
            "project_root": str(root),
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
            "session_id": identity,
            "turn_id": f"turn-{identity}",
            "tool_use_id": f"tool-{identity}",
            "agent": "codex",
        }
    )
    process = subprocess.Popen(
        [sys.executable, str(ADAPTER)],
        cwd=ROOT,
        env=_environment(launched_ns),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return process, launched_ns, payload


def _finish_actual_adapter(item: ActualProcess) -> float:
    process, launched_ns, payload = item
    stdout, stderr = process.communicate(payload)
    ended_ns = perf_counter_ns()
    if process.returncode != 0:
        raise ChildProcessError(stderr.strip())
    raw = json.loads(stdout)
    if not isinstance(raw, dict):
        raise ChildProcessError("actual adapter did not emit an object")
    return _elapsed_ms(launched_ns, ended_ns)


def run_actual_group(root: Path, concurrency: int) -> tuple[list[float], float]:
    started_ns = perf_counter_ns()
    processes = [_start_actual_adapter(root, index) for index in range(concurrency)]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        samples = list(executor.map(_finish_actual_adapter, processes))
    return samples, _elapsed_ms(started_ns, perf_counter_ns())
