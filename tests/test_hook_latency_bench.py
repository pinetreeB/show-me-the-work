from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "eval" / "hook_latency_bench.py"
PHASES = {
    "process_start",
    "import",
    "config_layout",
    "ledger_load",
    "gate_evaluation",
    "state_write",
    "total",
}


def test_hook_latency_bench_emits_cold_warm_and_concurrent_phase_receipt(
    tmp_path: Path,
) -> None:
    # Given
    output = tmp_path / "receipt.json"
    fixture = tmp_path / "fixture-ledger.json"
    _ = fixture.write_text('{"schema_version": 1}\n', encoding="utf-8")

    # When
    result = subprocess.run(
        [
            sys.executable,
            str(BENCH),
            "--fixture",
            str(fixture),
            "--samples",
            "1",
            "--concurrency",
            "2",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    # Then
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert json.loads(output.read_text(encoding="utf-8")) == receipt
    assert receipt["schema_version"] == 1
    assert receipt["fixture"]["bytes"] == fixture.stat().st_size
    assert receipt["fixture"]["statusless_invocations"] == 0
    assert receipt["settings"] == {"samples": 1, "concurrency": 2}
    assert set(receipt["scenarios"]) == {"cold", "warm", "load"}
    assert receipt["scenarios"]["cold"]["sample_count"] == 1
    assert receipt["scenarios"]["warm"]["sample_count"] == 1
    assert receipt["scenarios"]["load"]["sample_count"] == 2
    assert set(receipt["actual_adapter"]) == {"cold", "warm", "load"}
    assert receipt["actual_adapter"]["cold"]["sample_count"] == 1
    assert receipt["actual_adapter"]["warm"]["sample_count"] == 1
    assert receipt["actual_adapter"]["load"]["sample_count"] == 2
    for scenario in receipt["actual_adapter"].values():
        assert scenario["total"]["p50_ms"] >= 0
        assert scenario["total"]["p95_ms"] >= scenario["total"]["p50_ms"]
    for scenario in receipt["scenarios"].values():
        assert set(scenario["phases"]) == PHASES
        for stats in scenario["phases"].values():
            assert len(stats["samples_ms"]) == scenario["sample_count"]
            assert stats["p50_ms"] >= 0
            assert stats["p95_ms"] >= stats["p50_ms"]


def test_importing_cli_defers_hook_irrelevant_command_modules() -> None:
    # Given
    command_modules = (
        "smtw.brief",
        "smtw.check",
        "smtw.doctor",
        "smtw.goals",
        "smtw.init",
        "smtw.intent",
        "smtw.migrate",
        "smtw.quarantine",
        "smtw.scorecard",
        "smtw.versioning",
    )
    code = (
        "import json, sys\n"
        "import smtw.cli\n"
        f"names = {command_modules!r}\n"
        "print(json.dumps([name for name in names if name in sys.modules]))\n"
    )

    # When
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    # Then
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []
