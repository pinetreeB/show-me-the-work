from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias

from core.ledger import record_event


ROOT = Path(__file__).resolve().parents[1]

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def run_cli(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "fable_lite", *args],
        cwd=cwd or ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def git(project: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr


def init_repo(project: Path) -> None:
    git(project, "init")
    git(project, "config", "user.email", "test@example.com")
    git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    git(project, "add", ".")
    git(project, "commit", "-m", "init")


def read_json(path: Path) -> dict[str, JsonValue]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def test_check_reports_green_when_changed_files_have_successful_verification(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8", newline="\n")
    record_event({"project_root": str(tmp_path), "event": "prompt", "task_mode": "deep", "prompt": "app.py 수정", "agent": "codex"})
    record_event({"project_root": str(tmp_path), "event": "change", "path": "app.py", "kind": "code", "agent": "codex"})
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "verification",
            "command": "python -m pytest",
            "success": True,
            "evidence": "1 passed",
            "agent": "codex",
        }
    )

    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    assert result.returncode == 0
    assert "GREEN" in result.stdout
    assert "app.py" in result.stdout


def test_check_reports_red_for_unverified_scope_drift_r1_and_missing_sentinel(tmp_path: Path) -> None:
    init_repo(tmp_path)
    marker = tmp_path / "marker.txt"
    marker.write_text("start\n", encoding="utf-8", newline="\n")
    (tmp_path / "settings.py").write_text("DROP TABLE users;\n", encoding="utf-8", newline="\n")
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "app.py만 수정하고 완료 후 빈 파일 tmp/.done-x sentinel 생성",
            "agent": "codex",
        }
    )
    record_event({"project_root": str(tmp_path), "event": "change", "path": "settings.py", "kind": "code", "agent": "codex"})

    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex", "--since-file", str(marker)])

    assert result.returncode == 1
    assert "RED" in result.stdout
    assert "미검증 변경" in result.stdout
    assert "settings.py" in result.stdout
    assert "범위" in result.stdout
    assert "R1" in result.stdout
    assert "sentinel" in result.stdout


def test_record_event_writes_agent_jsonl_without_breaking_legacy_ledger(tmp_path: Path) -> None:
    record_event({"project_root": str(tmp_path), "event": "change", "path": "app.py", "kind": "code", "agent": "codex"})

    ledger = read_json(tmp_path / ".fable-lite" / "ledger.json")
    agent_log = tmp_path / ".fable-lite" / "agents" / "codex.jsonl"
    lines = agent_log.read_text(encoding="utf-8").splitlines()

    assert ledger["agent"] == "codex"
    assert ledger["changed_files_seen"] == ["app.py"]
    assert len(lines) == 1
    assert json.loads(lines[0])["agent"] == "codex"


def test_brief_prints_target_specific_delegation_rules() -> None:
    result = run_cli(
        [
            "brief",
            "--paths",
            "core/**,tests/**",
            "--verify-cmd",
            "python -m pytest tests/",
            "--sentinel",
            "tmp/.done-x",
            "--target",
            "agy",
        ]
    )

    assert result.returncode == 0
    assert "allowed_paths" in result.stdout
    assert "core/**" in result.stdout
    assert "python -m pytest tests/" in result.stdout
    assert "tmp/.done-x" in result.stdout
    assert "사후 check" in result.stdout
    assert "상세 규율" in result.stdout
