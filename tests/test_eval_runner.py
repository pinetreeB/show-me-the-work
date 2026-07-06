from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "eval" / "run_probes.py"

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def run_runner(output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def as_object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def as_list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def as_str(value: JsonValue) -> str:
    assert isinstance(value, str)
    return value


def load_report(path: Path) -> dict[str, JsonValue]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


@pytest.fixture(scope="module")
def runner_execution(tmp_path_factory: pytest.TempPathFactory) -> tuple[subprocess.CompletedProcess[str], Path, dict[str, JsonValue]]:
    output = tmp_path_factory.mktemp("probe-runner") / "probes-test.json"
    process = run_runner(output)
    report = load_report(output) if output.exists() else {}
    return process, output, report


def test_probe_runner_writes_json_and_ascii_summary(
    runner_execution: tuple[subprocess.CompletedProcess[str], Path, dict[str, JsonValue]],
) -> None:
    process, output, report = runner_execution
    assert process.returncode == 0, process.stderr
    process.stdout.encode("ascii")
    assert process.stdout.startswith("probes pass=")
    assert output.exists()

    summary = as_object(report["summary"])
    assert isinstance(summary["fail"], int)
    assert isinstance(summary["pass"], int)
    assert summary["pass"] > 0
    assert isinstance(summary["manual"], int)
    assert summary["manual"] > 0
    assert report["result"] in {"PASS", "FAIL"}


def test_probe_runner_reports_all_probe_ids_and_ab_shape(
    runner_execution: tuple[subprocess.CompletedProcess[str], Path, dict[str, JsonValue]],
) -> None:
    process, _, report = runner_execution
    assert process.returncode == 0, process.stderr

    results = as_list(report["results"])
    ids = {as_str(as_object(item)["id"]) for item in results}
    assert ids == {f"PRB-{number:02d}" for number in range(1, 19)}

    automatic = [as_object(item) for item in results if as_object(item)["status"] != "manual"]
    manual_ids = {
        as_str(as_object(item)["id"]) for item in results if as_object(item)["status"] == "manual"
    }
    assert {"PRB-01", "PRB-04", "PRB-11"}.issubset(manual_ids)
    assert {"PRB-03", "PRB-05", "PRB-08", "PRB-10", "PRB-16", "PRB-17"}.issubset(
        {as_str(item["id"]) for item in automatic}
    )

    for item in automatic:
        baseline = as_object(item["baseline"])
        fable_lite = as_object(item["fable_lite"])
        assert baseline["status"] == "pass"
        assert baseline["mode"] == "off"
        assert fable_lite["mode"] == "on"
        assert fable_lite["status"] in {"pass", "fail"}


def test_probe_runner_output_has_no_timestamp_dependent_default_name(
    runner_execution: tuple[subprocess.CompletedProcess[str], Path, dict[str, JsonValue]],
) -> None:
    process, _, report = runner_execution
    assert process.returncode == 0, process.stderr

    assert report["output_name"] == "probes-latest.json"
