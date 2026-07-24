"""v2.6.2 GOALS-03A/B — goals identity·authoring correctness (RED-first).

GOALS-03A (INV-04): peer open receipt는 current CLI caller identity의 근거로
쓰지 않는다. 우선순위: ①explicit --identity ②complete triplet ③exact active
1개 ④process environment unique match ⑤ambiguity error.

GOALS-03B (INV-05): goals authoring 예외는 R2 command-position parser가 인정한
실제 executable invocation만 허용한다 (echo/printf/python -c/comment/env
value/argument 문자열 불가).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core.contract import _is_goals_authoring, evaluate_pretool_contract
from core.ledger import JsonObject, record_event
from core.state_layout import state_dir
from goals.goals import GoalsCliError, resolve_identity


ROOT = Path(__file__).resolve().parents[1]


def _seed_exact_turn(
    root: Path, *, session_id: str, agent: str, host: str = "codex_cli"
) -> JsonObject:
    identity: JsonObject = {
        "project_root": str(root),
        "host": host,
        "session_id": session_id,
        "agent": agent,
        "turn_id": f"turn:{session_id}:{agent}",
        "attribution": "exact",
        "identity_synthetic": False,
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


def _seed_open_invocation(identity: JsonObject, invocation_id: str) -> None:
    _ = record_event(
        identity
        | {
            "event": "invocation",
            "invocation_id": invocation_id,
            "candidate_paths": [],
        }
    )


def _bare_args() -> argparse.Namespace:
    return argparse.Namespace(identity=None, host=None, session_id=None, agent=None)


def _identity_key(session_id: str, agent: str, host: str = "codex_cli") -> str:
    return f"{host}:{session_id}:{agent}"


# ---------------------------------------------------------------------------
# GOALS-03A — peer open receipt identity 추론 제거
# ---------------------------------------------------------------------------


def test_goals_03a_peer_open_receipt_does_not_resolve_identity(
    tmp_path: Path,
) -> None:
    # identity A·B active, B에 open invocation, caller hint 없음.
    _ = _seed_exact_turn(tmp_path, session_id="session-a", agent="codex-a")
    identity_b = _seed_exact_turn(tmp_path, session_id="session-b", agent="codex-b")
    _seed_open_invocation(identity_b, "peer-open-invocation")

    # 수정 전: receipt fallback이 B를 선택한다(RED). 수정 후: ambiguity error.
    with pytest.raises(GoalsCliError) as excinfo:
        _ = resolve_identity(str(tmp_path), _bare_args(), environ={})

    assert excinfo.value.code == "ambiguous_identity"
    assert excinfo.value.candidates == (
        "codex_cli:session-a:codex-a",
        "codex_cli:session-b:codex-b",
    )


def test_goals_03a_environment_unique_match_still_wins(tmp_path: Path) -> None:
    # 우선순위 ④는 유지된다: env unique match는 receipt 없이도 선택한다.
    _ = _seed_exact_turn(tmp_path, session_id="session-a", agent="codex-a")
    _ = _seed_exact_turn(tmp_path, session_id="session-b", agent="codex-b")

    resolved = resolve_identity(
        str(tmp_path),
        _bare_args(),
        environ={"CODEX_THREAD_ID": "session-b"},
    )

    assert resolved is not None
    assert resolved["session_id"] == "session-b"
    assert resolved["agent"] == "codex-b"


def test_goals_03a_single_exact_identity_still_auto_selects(tmp_path: Path) -> None:
    # 우선순위 ③ 유지: exact active 1개는 자동 선택.
    _ = _seed_exact_turn(tmp_path, session_id="only-session", agent="codex")

    resolved = resolve_identity(str(tmp_path), _bare_args(), environ={})

    assert resolved is not None
    assert resolved["session_id"] == "only-session"


def test_goals_03a_cli_plan_with_peer_receipt_leaves_checkpoints_untouched(
    tmp_path: Path,
) -> None:
    _ = _seed_exact_turn(tmp_path, session_id="session-a", agent="codex-a")
    identity_b = _seed_exact_turn(tmp_path, session_id="session-b", agent="codex-b")
    _seed_open_invocation(identity_b, "peer-open-invocation")
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

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "smtw",
            "goals",
            "plan",
            "--root",
            str(tmp_path),
            "--goal",
            "ship PR C",
            "--story",
            "goals correctness",
            "--verify-cmd",
            "python -m pytest",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert result.returncode == 2
    assert '"error": "ambiguous_identity"' in result.stdout
    # B(또는 A)의 checkpoint가 작성되지 않아야 한다.
    goals_dir = state_dir(str(tmp_path)) / "goals"
    assert not goals_dir.exists() or not any(goals_dir.iterdir())
    assert not (state_dir(str(tmp_path)) / "goals.json").exists()


# ---------------------------------------------------------------------------
# GOALS-03B — 실제 command-position authoring만 인정
# ---------------------------------------------------------------------------


def _needs_goals_payload(root: Path) -> JsonObject:
    identity = _seed_exact_turn(root, session_id="authoring-session", agent="codex")
    return identity | {"tool_name": "Bash"}


def test_goals_03b_echo_goals_plan_is_ordinary_mutation_blocked(
    tmp_path: Path,
) -> None:
    payload = _needs_goals_payload(tmp_path)

    # 수정 전: 문자열 검색이 "smtw goals plan"을 찾아 authoring으로 오인(RED).
    assert _is_goals_authoring(str(tmp_path), [], "echo smtw goals plan") is False
    decision = evaluate_pretool_contract(
        payload | {"command": "echo smtw goals plan && printf x > output.txt"}
    )

    assert decision["decision"] == "block"
    assert "N2 checkpoint required" in str(decision["reason"])


def test_goals_03b_actual_goals_plan_command_is_allowed(tmp_path: Path) -> None:
    payload = _needs_goals_payload(tmp_path)

    assert (
        _is_goals_authoring(str(tmp_path), [], "smtw goals plan --root .") is True
    )
    decision = evaluate_pretool_contract(
        payload
        | {
            "command": (
                "smtw goals plan --root . --goal goal "
                "--story story --verify-cmd pytest"
            )
        }
    )

    assert decision["decision"] == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "smtw goals plan --root . --goal g --story s --verify-cmd pytest",
        "smtw.exe goals plan --root .",
        "fable-lite goals plan --root .",
        "python -m smtw goals plan --root .",
        "python3 -m smtw goals plan --root .",
        "py -m smtw goals plan --root .",
        "python fable-lite-cli.py goals plan --root .",
        "python goals/goals.py plan --root .",
        "/usr/local/bin/smtw goals plan --root .",
        "smtw goals plan --root . && echo planned",
        "echo done && smtw goals plan --root .",
    ],
)
def test_goals_03b_accepts_real_executable_authoring(
    tmp_path: Path, command: str
) -> None:
    assert _is_goals_authoring(str(tmp_path), [], command) is True


@pytest.mark.parametrize(
    "command",
    [
        "echo smtw goals plan",
        "echo smtw goals plan && printf x > output.txt",
        "printf 'smtw goals plan'",
        "python -c \"print('smtw goals plan')\"",
        "# smtw goals plan",
        "MSG=\"smtw goals plan\" true",
        "grep \"smtw goals plan\" notes.txt",
        "cat smtw-goals-plan.txt",
    ],
)
def test_goals_03b_rejects_string_lookalikes(tmp_path: Path, command: str) -> None:
    assert _is_goals_authoring(str(tmp_path), [], command) is False


def test_goals_03b_goals_json_file_authoring_still_allowed(tmp_path: Path) -> None:
    # file_paths 기반 goals.json 직접 authoring 예외는 유지된다.
    goals_path = state_dir(str(tmp_path)) / "goals.json"
    assert (
        _is_goals_authoring(str(tmp_path), [str(goals_path)], "") is True
    )
