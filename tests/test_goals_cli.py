from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.state_layout import state_dir


ROOT = Path(__file__).resolve().parents[1]
GOALS = ROOT / "goals" / "goals.py"


def run_goals(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GOALS), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_goals_cli_creates_and_verifies_checkpoint(tmp_path: Path) -> None:
    plan = run_goals(
        "plan",
        "--root",
        str(tmp_path),
        "--goal",
        "페이지 만들기",
        "--story",
        "관리자 페이지 렌더",
        "--verify-cmd",
        "python -m pytest",
    )
    verify = run_goals(
        "verify",
        "--root",
        str(tmp_path),
        "--story",
        "관리자 페이지 렌더",
        "--evidence",
        "pytest green",
    )
    status = run_goals("status", "--root", str(tmp_path))

    assert plan.returncode == 0
    assert verify.returncode == 0
    assert status.returncode == 0
    data = json.loads(status.stdout)
    assert data["goal"] == "페이지 만들기"
    assert data["stories"][0]["verified"] is True
    goals_path = state_dir(tmp_path) / "goals.json"
    assert goals_path.exists()
    assert b"\r\n" not in goals_path.read_bytes()


def test_goals_cli_fail_opens_as_json_on_bad_invocation() -> None:
    result = run_goals()

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["fail_open"] is True
    assert "usage" not in result.stderr.lower()
