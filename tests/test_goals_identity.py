from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from core.contract import namespaced_contract_path
from core.gate_counters import needs_goals_block
from core.ledger import JsonObject, record_event


ROOT = Path(__file__).resolve().parents[1]
GOALS = ROOT / "goals" / "goals.py"


def _run_goals(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GOALS), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _identity_args(session_id: str, agent: str) -> tuple[str, ...]:
    return (
        "--host",
        "codex_cli",
        "--session-id",
        session_id,
        "--agent",
        agent,
    )


def _agent_key(session_id: str, agent: str) -> str:
    return f"codex_cli:{session_id}:{agent}"


def _goals_path(root: Path, session_id: str, agent: str) -> Path:
    filename = namespaced_contract_path(
        str(root),
        _agent_key(session_id, agent),
    ).name
    return root / ".fable-lite" / "goals" / filename


def _seed_turn(root: Path, session_id: str, agent: str) -> JsonObject:
    identity: JsonObject = {
        "project_root": str(root),
        "host": "codex_cli",
        "session_id": session_id,
        "agent": agent,
        "turn_id": f"turn:{session_id}",
    }
    _ = record_event(
        identity
        | {
            "event": "prompt",
            "prompt": "implement two stories",
            "task_mode": "normal",
            "needs_goals": True,
        }
    )
    return identity


def _plan(root: Path, session_id: str, agent: str, goal: str) -> None:
    result = _run_goals(
        "plan",
        "--root",
        str(root),
        "--goal",
        goal,
        "--story",
        f"{goal} story",
        "--verify-cmd",
        "python -m pytest",
        *_identity_args(session_id, agent),
    )
    assert result.returncode == 0
    assert json.loads(result.stdout).get("fail_open") is not True


def _status(root: Path, session_id: str, agent: str) -> dict[str, object]:
    result = _run_goals(
        "status",
        "--root",
        str(root),
        *_identity_args(session_id, agent),
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert isinstance(data, dict)
    return data


def test_identity_plans_do_not_overwrite_each_other(tmp_path: Path) -> None:
    first_path = _goals_path(tmp_path, "session-a", "codex-a")
    second_path = _goals_path(tmp_path, "session-b", "codex-b")

    _plan(tmp_path, "session-a", "codex-a", "left goal")
    first_bytes = first_path.read_bytes()
    _plan(tmp_path, "session-b", "codex-b", "worker goal")

    assert first_path != second_path
    assert first_path.read_bytes() == first_bytes
    assert json.loads(first_path.read_text(encoding="utf-8"))["goal"] == "left goal"
    assert json.loads(second_path.read_text(encoding="utf-8"))["goal"] == "worker goal"
    assert (tmp_path / ".fable-lite" / "goals.json").exists() is False

    assert _status(tmp_path, "session-a", "codex-a")["goal"] == "left goal"
    assert _status(tmp_path, "session-b", "codex-b")["goal"] == "worker goal"


def test_n2_checks_only_the_active_identity_goal(tmp_path: Path) -> None:
    first = _seed_turn(tmp_path, "session-a", "codex-a")
    second = _seed_turn(tmp_path, "session-b", "codex-b")
    _plan(tmp_path, "session-a", "codex-a", "left goal")

    assert needs_goals_block(first) is False
    assert needs_goals_block(second) is True

    _plan(tmp_path, "session-b", "codex-b", "worker goal")

    assert needs_goals_block(second) is False


def test_n2_ignores_shared_legacy_goal_while_exact_identities_overlap(
    tmp_path: Path,
) -> None:
    _ = _seed_turn(tmp_path, "session-a", "codex-a")
    second = _seed_turn(tmp_path, "session-b", "codex-b")
    legacy = tmp_path / ".fable-lite" / "goals.json"
    legacy.write_text('{"goal": "legacy"}\n', encoding="utf-8")

    assert needs_goals_block(second) is True


def test_n2_keeps_legacy_goal_fallback_for_one_active_identity(
    tmp_path: Path,
) -> None:
    identity = _seed_turn(tmp_path, "legacy-session", "codex")
    legacy = tmp_path / ".fable-lite" / "goals.json"
    legacy.write_text('{"goal": "legacy"}\n', encoding="utf-8")

    assert needs_goals_block(identity) is False


def test_identity_cli_reads_legacy_goal_then_migrates_on_verify(
    tmp_path: Path,
) -> None:
    _ = _seed_turn(tmp_path, "legacy-session", "codex")
    legacy_plan = _run_goals(
        "plan",
        "--root",
        str(tmp_path),
        "--goal",
        "legacy goal",
        "--story",
        "legacy story",
        "--verify-cmd",
        "python -m pytest",
    )
    assert legacy_plan.returncode == 0

    assert _status(tmp_path, "legacy-session", "codex")["goal"] == "legacy goal"

    verified = _run_goals(
        "verify",
        "--root",
        str(tmp_path),
        "--story",
        "legacy story",
        "--evidence",
        "pytest green",
        *_identity_args("legacy-session", "codex"),
    )

    assert verified.returncode == 0
    namespaced = _goals_path(tmp_path, "legacy-session", "codex")
    migrated = json.loads(namespaced.read_text(encoding="utf-8"))
    assert migrated["stories"][0]["verified"] is True
