# /// script
# requires-python = ">=3.12"
# ///
# How to run: python eval/hook_latency_bench.py --samples 7 --concurrency 4
from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from time import perf_counter_ns
from typing import Final, TypeAlias


_PROCESS_STARTED_NS = perf_counter_ns()

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE: Final = ROOT / "tmp" / "friction-fix" / "fixture-ledger-8mb.json"
LAUNCHED_NS_ENV: Final = "SMTW_HOOK_BENCH_LAUNCHED_NS"
PHASES: Final = (
    "process_start",
    "import",
    "config_layout",
    "ledger_load",
    "gate_evaluation",
    "state_write",
    "total",
)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
PhaseSample: TypeAlias = dict[str, float]


@dataclass(frozen=True, slots=True)
class BenchConfig:
    fixture: Path
    samples: int
    concurrency: int
    output: Path | None


def _elapsed_ms(started_ns: int, ended_ns: int) -> float:
    return (ended_ns - started_ns) / 1_000_000


def _worker(root: Path) -> PhaseSample:
    launched_ns = int(os.environ[LAUNCHED_NS_ENV])
    _ = importlib.import_module("smtw.cli")

    imported_ns = perf_counter_ns()
    from adapters.claude_code.project_config import load_project_config  # noqa: PLC0415
    from core.state_layout import inspect_state_layout_details  # noqa: PLC0415

    _ = load_project_config(root)
    _ = inspect_state_layout_details(root)
    config_ns = perf_counter_ns()
    from core.ledger import load_ledger, record_event  # noqa: PLC0415

    _ = load_ledger({"project_root": str(root)})
    ledger_ns = perf_counter_ns()
    from core.contract import evaluate_pretool_contract  # noqa: PLC0415

    identity: JsonObject = {
        "project_root": str(root),
        "host": "codex_cli",
        "session_id": f"hook-bench-{os.getpid()}",
        "agent": "codex",
        "turn_id": f"hook-bench-turn-{os.getpid()}",
        "attribution": "exact",
        "identity_synthetic": False,
    }
    _ = evaluate_pretool_contract(
        identity | {"tool_name": "Bash", "command": "git status --short"}
    )
    gate_ns = perf_counter_ns()
    write_root = root / "write-target"
    _ = record_event(
        identity
        | {
            "project_root": str(write_root),
            "event": "prompt",
            "task_mode": "routine",
            "prompt": "hook latency benchmark",
            "needs_goals": False,
        }
    )
    write_ns = perf_counter_ns()
    return {
        "process_start": _elapsed_ms(launched_ns, _PROCESS_STARTED_NS),
        "import": _elapsed_ms(_PROCESS_STARTED_NS, imported_ns),
        "config_layout": _elapsed_ms(imported_ns, config_ns),
        "ledger_load": _elapsed_ms(config_ns, ledger_ns),
        "gate_evaluation": _elapsed_ms(ledger_ns, gate_ns),
        "state_write": _elapsed_ms(gate_ns, write_ns),
        "total": _elapsed_ms(launched_ns, write_ns),
    }


def _prepare_project(root: Path, fixture: Path) -> None:
    _ = root.mkdir(parents=True)
    _ = (root / ".git").mkdir()
    _ = (root / ".smtw.toml").write_text(
        "schema_version = 1\nsupervision = true\n",
        encoding="utf-8",
    )
    state = root / ".fable-lite"
    _ = state.mkdir()
    _ = shutil.copyfile(fixture, state / "ledger.json")
    _ = (root / "write-target").mkdir()


def _start_worker(root: Path) -> subprocess.Popen[str]:
    launched_ns = perf_counter_ns()
    environment = {
        **os.environ,
        LAUNCHED_NS_ENV: str(launched_ns),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(ROOT),
    }
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--worker-root", str(root)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def _finish_worker(process: subprocess.Popen[str]) -> PhaseSample:
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise ChildProcessError(stderr.strip())
    raw: JsonObject = json.loads(stdout)
    sample: PhaseSample = {}
    for phase in PHASES:
        value = raw.get(phase)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ChildProcessError(f"worker phase is not numeric: {phase}")
        sample[phase] = float(value)
    return sample


def _run_group(root: Path, concurrency: int) -> tuple[list[PhaseSample], float]:
    started_ns = perf_counter_ns()
    processes = [_start_worker(root) for _ in range(concurrency)]
    samples = [_finish_worker(process) for process in processes]
    return samples, _elapsed_ms(started_ns, perf_counter_ns())


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _stats(values: list[float]) -> JsonObject:
    return {
        "samples_ms": [round(value, 3) for value in values],
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
    }


def _scenario(samples: list[PhaseSample], walls: list[float]) -> JsonObject:
    return {
        "sample_count": len(samples),
        "phases": {
            phase: _stats([sample[phase] for sample in samples]) for phase in PHASES
        },
        "group_wall": _stats(walls),
    }


def _run_benchmark(config: BenchConfig) -> JsonObject:
    with tempfile.TemporaryDirectory(
        prefix=".w4b-hook-latency-",
        dir=config.fixture.parent,
    ) as raw:
        base = Path(raw)
        cold_samples: list[PhaseSample] = []
        cold_walls: list[float] = []
        for index in range(config.samples):
            root = base / f"cold-{index}"
            _prepare_project(root, config.fixture)
            samples, wall = _run_group(root, 1)
            cold_samples.extend(samples)
            cold_walls.append(wall)

        warm_root = base / "warm"
        _prepare_project(warm_root, config.fixture)
        _ = _run_group(warm_root, 1)
        warm_samples: list[PhaseSample] = []
        warm_walls: list[float] = []
        for _index in range(config.samples):
            samples, wall = _run_group(warm_root, 1)
            warm_samples.extend(samples)
            warm_walls.append(wall)

        load_root = base / "load"
        _prepare_project(load_root, config.fixture)
        _ = _run_group(load_root, 1)
        load_samples: list[PhaseSample] = []
        load_walls: list[float] = []
        for _index in range(config.samples):
            samples, wall = _run_group(load_root, config.concurrency)
            load_samples.extend(samples)
            load_walls.append(wall)

    fixture_bytes = config.fixture.read_bytes()
    return {
        "schema_version": 1,
        "fixture": {
            "path": str(config.fixture.resolve()),
            "bytes": len(fixture_bytes),
            "sha256": sha256(fixture_bytes).hexdigest(),
        },
        "settings": {
            "samples": config.samples,
            "concurrency": config.concurrency,
        },
        "method": {
            "cold": "fresh process and fresh fixture copy per sample",
            "warm": "fresh processes after one untimed cache-primer run",
            "load": "fresh concurrent processes sharing one warmed project",
            "import": "process-local bootstrap plus import smtw.cli",
            "state_write": "record_event against a separate valid temporary state tree",
        },
        "scenarios": {
            "cold": _scenario(cold_samples, cold_walls),
            "warm": _scenario(warm_samples, warm_walls),
            "load": _scenario(load_samples, load_walls),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure SMTW hook latency phases.")
    _ = parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    _ = parser.add_argument("--samples", type=int, default=7)
    _ = parser.add_argument("--concurrency", type=int, default=4)
    _ = parser.add_argument("--output", type=Path)
    _ = parser.add_argument("--worker-root", type=Path, help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    worker_root = options.worker_root
    if isinstance(worker_root, Path):
        print(json.dumps(_worker(worker_root), sort_keys=True))
        return 0
    samples = options.samples
    concurrency = options.concurrency
    fixture = options.fixture
    output = options.output
    if not isinstance(samples, int) or not isinstance(concurrency, int):
        parser.error("--samples and --concurrency must be integers")
    if samples < 1 or concurrency < 1:
        parser.error("--samples and --concurrency must be positive")
    if not isinstance(fixture, Path):
        parser.error("--fixture must be a path")
    if output is not None and not isinstance(output, Path):
        parser.error("--output must be a path")
    config = BenchConfig(
        fixture=fixture.resolve(),
        samples=samples,
        concurrency=concurrency,
        output=output,
    )
    receipt = _run_benchmark(config)
    rendered = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if config.output is not None:
        _ = config.output.parent.mkdir(parents=True, exist_ok=True)
        _ = config.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
