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
import sys
import tempfile
from time import perf_counter_ns
from typing import Final, TypeAlias


ROOT: Final = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.hook_latency_processes import run_actual_group, run_phase_group  # noqa: E402
from eval.hook_latency_receipt import (  # noqa: E402
    actual_scenario,
    phase_scenario,
    statusless_invocations,
)


_PROCESS_STARTED_NS = perf_counter_ns()
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
            samples, wall = run_phase_group(root, 1, PHASES)
            cold_samples.extend(samples)
            cold_walls.append(wall)

        warm_root = base / "warm"
        _prepare_project(warm_root, config.fixture)
        _ = run_phase_group(warm_root, 1, PHASES)
        warm_samples: list[PhaseSample] = []
        warm_walls: list[float] = []
        for _index in range(config.samples):
            samples, wall = run_phase_group(warm_root, 1, PHASES)
            warm_samples.extend(samples)
            warm_walls.append(wall)

        load_root = base / "load"
        _prepare_project(load_root, config.fixture)
        _ = run_phase_group(load_root, 1, PHASES)
        load_samples: list[PhaseSample] = []
        load_walls: list[float] = []
        for _index in range(config.samples):
            samples, wall = run_phase_group(load_root, config.concurrency, PHASES)
            load_samples.extend(samples)
            load_walls.append(wall)

        actual_cold: list[float] = []
        actual_cold_walls: list[float] = []
        for index in range(config.samples):
            root = base / f"actual-cold-{index}"
            _prepare_project(root, config.fixture)
            samples, wall = run_actual_group(root, 1)
            actual_cold.extend(samples)
            actual_cold_walls.append(wall)

        actual_warm_root = base / "actual-warm"
        _prepare_project(actual_warm_root, config.fixture)
        _ = run_actual_group(actual_warm_root, 1)
        actual_warm: list[float] = []
        actual_warm_walls: list[float] = []
        for _index in range(config.samples):
            samples, wall = run_actual_group(actual_warm_root, 1)
            actual_warm.extend(samples)
            actual_warm_walls.append(wall)

        actual_load_root = base / "actual-load"
        _prepare_project(actual_load_root, config.fixture)
        _ = run_actual_group(actual_load_root, 1)
        actual_load: list[float] = []
        actual_load_walls: list[float] = []
        for _index in range(config.samples):
            samples, wall = run_actual_group(actual_load_root, config.concurrency)
            actual_load.extend(samples)
            actual_load_walls.append(wall)

    fixture_bytes = config.fixture.read_bytes()
    fixture_json: JsonValue = json.loads(fixture_bytes)
    return {
        "schema_version": 1,
        "fixture": {
            "path": str(config.fixture.resolve()),
            "bytes": len(fixture_bytes),
            "sha256": sha256(fixture_bytes).hexdigest(),
            "statusless_invocations": statusless_invocations(fixture_json),
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
            "cold": phase_scenario(cold_samples, cold_walls, PHASES),
            "warm": phase_scenario(warm_samples, warm_walls, PHASES),
            "load": phase_scenario(load_samples, load_walls, PHASES),
        },
        "actual_adapter": {
            "cold": actual_scenario(actual_cold, actual_cold_walls),
            "warm": actual_scenario(actual_warm, actual_warm_walls),
            "load": actual_scenario(actual_load, actual_load_walls),
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
