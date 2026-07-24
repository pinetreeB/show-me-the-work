from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

from core.contract import namespaced_contract_path
from core.contract import evaluate_pretool_contract
from core.gate_counters import block_goals_once, needs_goals_block
from core.ledger import JsonObject, load_ledger, record_event
from core.state_layout import state_dir


ROOT = Path(__file__).resolve().parents[1]
LEGACY_GOALS = ROOT / "goals" / "goals.py"


def _run_legacy_goals(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LEGACY_GOALS), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _run_smtw(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "smtw", "goals", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _seed_turn(
    root: Path,
    *,
    host: str = "codex_cli",
    session_id: str,
    agent: str,
    synthetic: bool = False,
) -> JsonObject:
    identity: JsonObject = {
        "project_root": str(root),
        "host": host,
        "session_id": session_id,
        "agent": agent,
        "turn_id": f"turn:{session_id}:{agent}",
        "attribution": "legacy_default" if synthetic else "exact",
        "identity_synthetic": synthetic,
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


def _identity_key(
    session_id: str, agent: str, host: str = "codex_cli"
) -> str:
    return f"{host}:{session_id}:{agent}"


def _goals_path(
    root: Path,
    session_id: str,
    agent: str,
    host: str = "codex_cli",
) -> Path:
    filename = namespaced_contract_path(
        str(root), _identity_key(session_id, agent, host)
    ).name
    return state_dir(root) / "goals" / filename


def _plan_args(root: Path, goal: str = "ship PR E") -> tuple[str, ...]:
    return (
        "plan",
        "--root",
        str(root),
        "--goal",
        goal,
        "--story",
        "identity-aware plan",
        "--verify-cmd",
        "python -m pytest",
    )


def _json_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def test_identityless_plan_auto_selects_only_active_exact_identity(
    tmp_path: Path,
) -> None:
    identity = _seed_turn(
        tmp_path, session_id="single-session", agent="codex"
    )

    result = _run_legacy_goals(*_plan_args(tmp_path))

    assert result.returncode == 0
    assert _json_output(result).get("error") is None
    assert _goals_path(tmp_path, "single-session", "codex").is_file()
    assert not (state_dir(tmp_path) / "goals.json").exists()
    assert needs_goals_block(identity) is False


def test_identityless_plan_rejects_two_exact_identities_without_hint(
    tmp_path: Path,
) -> None:
    _ = _seed_turn(tmp_path, session_id="session-a", agent="codex-a")
    _ = _seed_turn(tmp_path, session_id="session-b", agent="codex-b")

    result = _run_legacy_goals(*_plan_args(tmp_path))

    assert result.returncode != 0
    payload = _json_output(result)
    assert payload["error"] == "ambiguous_identity"
    assert payload["candidates"] == [
        "codex_cli:session-a:codex-a",
        "codex_cli:session-b:codex-b",
    ]
    assert not (state_dir(tmp_path) / "goals.json").exists()


def test_host_session_environment_selects_one_of_two_exact_identities(
    tmp_path: Path,
) -> None:
    _ = _seed_turn(tmp_path, session_id="session-a", agent="codex-a")
    selected = _seed_turn(
        tmp_path, session_id="session-b", agent="codex-b"
    )
    env = os.environ.copy()
    env["CODEX_THREAD_ID"] = "session-b"

    result = _run_smtw(*_plan_args(tmp_path), env=env)

    assert result.returncode == 0
    assert _goals_path(tmp_path, "session-b", "codex-b").is_file()
    assert not _goals_path(tmp_path, "session-a", "codex-a").exists()
    assert not (state_dir(tmp_path) / "goals.json").exists()
    assert needs_goals_block(selected) is False


def test_open_hook_receipt_does_not_select_one_of_two_exact_identities(
    tmp_path: Path,
) -> None:
    # GOALS-03A (INV-04): peer open receipt는 current CLI caller identity의
    # 근거로 쓰지 않는다. 현재 CLI는 PreToolUse invocation 등록 전에 N2 판정을
    # 받을 수 있어 open receipt는 peer의 것일 수 있다.
    _ = _seed_turn(tmp_path, session_id="session-a", agent="codex-a")
    selected = _seed_turn(
        tmp_path, session_id="session-b", agent="codex-b"
    )
    _ = record_event(
        selected
        | {
            "event": "invocation",
            "invocation_id": "goals-plan-receipt",
            "candidate_paths": [],
        }
    )
    env = os.environ.copy()
    for variable in (
        "SMTW_HOST",
        "SMTW_SESSION_ID",
        "SMTW_AGENT",
        "CODEX_THREAD_ID",
        "CODEX_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_SESSION_ID",
        "ANTIGRAVITY_CONVERSATION_ID",
    ):
        env.pop(variable, None)

    result = _run_smtw(*_plan_args(tmp_path), env=env)

    assert result.returncode == 2
    payload = _json_output(result)
    assert payload["error"] == "ambiguous_identity"
    assert not _goals_path(tmp_path, "session-b", "codex-b").exists()
    assert not _goals_path(tmp_path, "session-a", "codex-a").exists()
    assert needs_goals_block(selected) is True


def test_n2_denial_contains_complete_copyable_identity_command(
    tmp_path: Path,
) -> None:
    identity = _seed_turn(
        tmp_path, session_id="command-session", agent="codex"
    )

    decision = block_goals_once(identity)

    assert decision["decision"] == "block"
    reason = str(decision["reason"])
    assert "[smtw] N2 checkpoint required." in reason
    assert "smtw goals plan" in reason
    assert f"--root {shlex.quote(str(tmp_path.resolve()))}" in reason
    assert "--identity codex_cli:command-session:codex" in reason
    assert "--goal '<goal>'" in reason
    assert "--story '<story>'" in reason
    assert "--verify-cmd '<verification-command>'" in reason
    assert "placeholders are not completion evidence" in reason

    authoring = evaluate_pretool_contract(
        identity
        | {
            "tool_name": "Bash",
            "command": (
                "smtw goals plan --root . "
                "--identity codex_cli:command-session:codex "
                "--goal goal --story story --verify-cmd pytest"
            ),
        }
    )
    assert authoring["decision"] == "allow"


def test_no_active_turn_keeps_legacy_single_agent_fallback(
    tmp_path: Path,
) -> None:
    result = _run_smtw(*_plan_args(tmp_path, "legacy goal"))

    assert result.returncode == 0
    legacy = state_dir(tmp_path) / "goals.json"
    assert legacy.is_file()
    assert json.loads(legacy.read_text(encoding="utf-8"))["goal"] == "legacy goal"


def test_wrong_explicit_identity_is_rejected_with_active_candidate(
    tmp_path: Path,
) -> None:
    _ = _seed_turn(tmp_path, session_id="session-a", agent="codex-a")

    result = _run_smtw(
        *_plan_args(tmp_path),
        "--identity",
        "codex_cli:session-b:codex-b",
    )

    assert result.returncode == 2
    payload = _json_output(result)
    assert payload["error"] == "wrong_identity"
    assert payload["candidates"] == ["codex_cli:session-a:codex-a"]
    assert not (state_dir(tmp_path) / "goals").exists()
    assert not (state_dir(tmp_path) / "goals.json").exists()


def test_foreign_identity_checkpoint_cannot_satisfy_another_turn(
    tmp_path: Path,
) -> None:
    first = _seed_turn(tmp_path, session_id="session-a", agent="codex-a")
    second = _seed_turn(tmp_path, session_id="session-b", agent="codex-b")

    result = _run_smtw(
        *_plan_args(tmp_path, "first goal"),
        "--identity",
        _identity_key("session-a", "codex-a"),
    )

    assert result.returncode == 0
    assert needs_goals_block(first) is False
    assert needs_goals_block(second) is True
    assert not _goals_path(tmp_path, "session-b", "codex-b").exists()
    second_status = _run_smtw(
        "status",
        "--root",
        str(tmp_path),
        "--identity",
        _identity_key("session-b", "codex-b"),
    )
    assert second_status.returncode == 0
    assert _json_output(second_status)["goal"] == ""


def test_plan_after_n2_block_immediately_recovers_without_cap_allow(
    tmp_path: Path,
) -> None:
    identity = _seed_turn(
        tmp_path, session_id="recovery-session", agent="codex"
    )
    blocked = block_goals_once(identity)
    assert blocked["decision"] == "block"

    planned = _run_smtw(*_plan_args(tmp_path, "recovery goal"))

    assert planned.returncode == 0
    assert needs_goals_block(identity) is False
    recovered = block_goals_once(identity)
    assert recovered["decision"] == "allow"
    assert recovered["message"] == "goals checkpoint is present"
    turn = load_ledger(identity)["active_turns"][
        _identity_key("recovery-session", "codex")
    ]
    assert isinstance(turn, dict)
    assert turn["goals_blocks"] == 1


def test_identity_and_triplet_must_match_when_both_are_given(
    tmp_path: Path,
) -> None:
    identity = _identity_key("matching-session", "codex")
    matching = _run_smtw(
        *_plan_args(tmp_path),
        "--identity",
        identity,
        "--host",
        "codex_cli",
        "--session-id",
        "matching-session",
        "--agent",
        "codex",
    )
    mismatch = _run_smtw(
        "status",
        "--root",
        str(tmp_path),
        "--identity",
        identity,
        "--host",
        "codex_cli",
        "--session-id",
        "other-session",
        "--agent",
        "codex",
    )

    assert matching.returncode == 0
    assert mismatch.returncode == 2
    assert _json_output(mismatch)["error"] == "identity_mismatch"


def test_partial_identity_triplet_is_an_explicit_error(tmp_path: Path) -> None:
    result = _run_smtw(
        "status",
        "--root",
        str(tmp_path),
        "--host",
        "codex_cli",
        "--session-id",
        "missing-agent",
    )

    assert result.returncode == 2
    assert _json_output(result)["error"] == "incomplete_identity"


def test_synthetic_active_identity_never_reports_checkpoint_success(
    tmp_path: Path,
) -> None:
    _ = _seed_turn(
        tmp_path,
        session_id="default",
        agent="codex",
        synthetic=True,
    )

    result = _run_smtw(*_plan_args(tmp_path))

    assert result.returncode == 2
    assert _json_output(result)["error"] == "synthetic_identity"
    assert not (state_dir(tmp_path) / "goals.json").exists()
    assert not (state_dir(tmp_path) / "goals").exists()


def test_canonical_plan_verify_and_status_share_auto_selected_identity(
    tmp_path: Path,
) -> None:
    _ = _seed_turn(tmp_path, session_id="cli-session", agent="codex")
    planned = _run_smtw(*_plan_args(tmp_path, "canonical goal"))
    verified = _run_smtw(
        "verify",
        "--root",
        str(tmp_path),
        "--story",
        "identity-aware plan",
        "--evidence",
        "pytest green",
    )
    status = _run_smtw("status", "--root", str(tmp_path))

    assert planned.returncode == verified.returncode == status.returncode == 0
    payload = _json_output(status)
    assert payload["goal"] == "canonical goal"
    stories = payload["stories"]
    assert isinstance(stories, list)
    assert isinstance(stories[0], dict)
    assert stories[0]["verified"] is True
    assert stories[0]["evidence"] == "pytest green"
