from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from core.ambiguity import JsonValue, evaluate_ambiguity
from core.intent import intent_path
from core.ledger import load_ledger, record_event
from core.ledger_storage import ledger_path


ROOT = Path(__file__).resolve().parents[1]

def run_cli(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-m", "fable_lite", *args],
        cwd=cwd or ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": python_path},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def intent_result(tmp_path: Path, prompt: str, requested_paths: list[str] | None = None) -> dict[str, JsonValue]:
    result = evaluate_ambiguity(
        {
            "project_root": str(tmp_path),
            "prompt": prompt,
            "requested_paths": requested_paths or [],
        }
    )
    assert isinstance(result, dict)
    return result


def signal_names(result: dict[str, JsonValue]) -> set[str]:
    signals = result["signals"]
    assert isinstance(signals, list)
    return {str(signal) for signal in signals}


def test_ambiguity_flags_only_when_two_or_more_signals_match(tmp_path: Path) -> None:
    cases = {
        "이거 알아서 고쳐줘": {"pronoun_reference", "delegation"},
        "어떻게 좀 바꿔줘": {"missing_target", "delegation"},
        "고쳐줘": {"missing_target", "ultra_short"},
        "여기 적당히 만들어줘": {"pronoun_reference", "delegation"},
    }

    for prompt, expected_signals in cases.items():
        result = intent_result(tmp_path, prompt)

        assert result["ambiguous"] is True, prompt
        assert expected_signals.issubset(signal_names(result)), prompt


def test_ambiguity_never_flags_absolute_no_flag_conditions(tmp_path: Path) -> None:
    goals_dir = tmp_path / "goals-present" / ".fable-lite"
    goals_dir.mkdir(parents=True)
    (goals_dir / "goals.json").write_text("{}", encoding="utf-8")
    intent_dir = tmp_path / "intent-present" / ".fable-lite"
    intent_dir.mkdir(parents=True)
    (intent_dir / "intent.json").write_text("{}", encoding="utf-8")

    cases = [
        (tmp_path / "quick", "이거 왜 안 돼?", []),
        (tmp_path / "explicit-path", "app.py 고쳐줘", ["app.py"]),
        (tmp_path / "goals-present", "이거 알아서 고쳐줘", []),
        (tmp_path / "intent-present", "이거 알아서 고쳐줘", []),
        (tmp_path / "skip-phrase", "묻지 말고 이거 알아서 고쳐줘", []),
    ]

    for root, prompt, requested_paths in cases:
        root.mkdir(exist_ok=True)
        result = evaluate_ambiguity(
            {
                "project_root": str(root),
                "prompt": prompt,
                "requested_paths": requested_paths,
            }
        )

        assert result["ambiguous"] is False, prompt


def test_ambiguity_does_not_flag_concrete_object_single_signal(tmp_path: Path) -> None:
    result = intent_result(tmp_path, "로그인 버튼 고쳐줘")

    assert result["ambiguous"] is False
    assert "missing_target" not in signal_names(result)


def test_ambiguity_score_counts_matched_signals(tmp_path: Path) -> None:
    cases = [
        ("왜 이 함수에서 TypeError가 발생해?", 0),
        ("로그인 버튼 색상과 라벨을 적당히 수정해줘", 1),
        ("고쳐줘", 2),
        ("이거 고쳐줘", 3),
    ]

    for prompt, expected_score in cases:
        result = intent_result(tmp_path, prompt)

        assert result["ambiguity_score"] == expected_score, prompt
        assert result["ambiguous"] is (expected_score >= 2), prompt


def test_ledger_records_intent_required_and_resets_intent_blocks_on_new_prompt(tmp_path: Path) -> None:
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "이거 알아서 고쳐줘",
            "intent_required": True,
            "ambiguity_score": 3,
        }
    )
    ledger = load_ledger({"project_root": str(tmp_path)})
    ledger["intent_blocks"] = 2
    ledger_path(str(tmp_path)).write_text(
        json.dumps(ledger, ensure_ascii=False), encoding="utf-8"
    )

    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "app.py 고쳐줘",
            "intent_required": False,
            "ambiguity_score": 0,
        }
    )
    updated = load_ledger({"project_root": str(tmp_path)})

    assert updated["intent_required"] is False
    assert updated["ambiguity_score"] == 0
    assert updated["intent_blocks"] == 0


def test_intent_cli_set_show_clear_schema(tmp_path: Path) -> None:
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "이거 알아서 고쳐줘",
            "intent_required": True,
            "ambiguity_score": 3,
        }
    )
    set_result = run_cli(
        [
            "intent",
            "set",
            "--root",
            str(tmp_path),
            "--goal",
            "로그인 버튼 오류 수정",
            "--scope",
            "app.py,templates/login.html",
            "--non-goal",
            "데이터베이스 스키마 변경",
            "--assumed",
            "--confirmed-at-prompt",
            "이거 알아서 고쳐줘",
        ]
    )
    show_result = run_cli(["intent", "show", "--root", str(tmp_path)])

    assert set_result.returncode == 0, set_result.stderr
    assert show_result.returncode == 0
    data = json.loads(show_result.stdout)
    assert data == {
        "goal": "로그인 버튼 오류 수정",
        "scope": ["app.py", "templates/login.html"],
        "non_goals": ["데이터베이스 스키마 변경"],
        "assumed": True,
        "confirmed_at_prompt": "이거 알아서 고쳐줘",
        "ambiguity_score": 3,
    }

    clear_result = run_cli(["intent", "clear", "--root", str(tmp_path)])
    show_after_clear = run_cli(["intent", "show", "--root", str(tmp_path)])

    assert clear_result.returncode == 0
    assert json.loads(show_after_clear.stdout) == {}
    assert not intent_path(str(tmp_path)).exists()


def test_root_launcher_runs_from_arbitrary_cwd_without_pythonpath(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(ROOT / "fable-lite-cli.py"), "intent", "show", "--root", "."],
        cwd=tmp_path,
        env={**env, "PYTHONIOENCODING": "utf-8"},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
