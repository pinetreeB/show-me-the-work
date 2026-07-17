from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.claude_code.bootstrap import HookContext, fail_open
from adapters.claude_code.common import project_root, transcript_last_assistant_text
from claude_hook_support import (
    HookHarness,
    JsonObject,
    JsonValue,
    ledger_path,
    read_json,
    registry_path,
    write_config,
)


def _prompt(root: Path, session_id: str, text: str) -> JsonObject:
    return {
        "cwd": str(root),
        "hook_event_name": "UserPromptSubmit",
        "prompt": text,
        "session_id": session_id,
        "prompt_id": f"prompt-{session_id}",
    }


@pytest.mark.parametrize("supervision", [None, False, 0, 1, "true", [], {}])
def test_inactive_config_values_are_quiet_and_project_stateless(
    tmp_path: Path,
    supervision: JsonValue,
) -> None:
    # Given: a project config whose supervision value is not exact boolean true.
    root = tmp_path / "project"
    root.mkdir()
    write_config(root, supervision)
    data_dir = tmp_path / "plugin-data"
    harness = HookHarness(root, root, data_dir, profile_imports=True)

    # When: the earliest Claude hook runs.
    result = harness.run(
        "user_prompt_submit.py", _prompt(root, "inactive", "app.py 수정")
    )

    # Then: it is silent, imports no shared core, and creates no runtime state.
    assert result.stdout.strip() == "{}"
    assert "adapters.claude_code.common" not in result.stderr
    assert "core." not in result.stderr
    assert ledger_path(root).exists() is False
    assert data_dir.exists() is False


def test_absent_config_and_exact_home_are_hard_off(tmp_path: Path) -> None:
    # Given: one ordinary inactive directory and one exact home with valid config.
    inactive = tmp_path / "inactive"
    home = tmp_path / "home"
    inactive.mkdir()
    home.mkdir()
    write_config(home)
    inactive_data = tmp_path / "inactive-data"
    home_data = tmp_path / "home-data"

    # When: UserPromptSubmit runs in both locations.
    inactive_result = HookHarness(inactive, inactive, inactive_data).run(
        "user_prompt_submit.py",
        _prompt(inactive, "inactive", "app.py 수정"),
    )
    home_result = HookHarness(home, home, home_data, home=home).run(
        "user_prompt_submit.py",
        _prompt(home, "home", "app.py 수정"),
    )

    # Then: both paths are exact no-ops with no adapter or project state.
    assert inactive_result.output == {}
    assert home_result.output == {}
    assert ledger_path(inactive).exists() is False
    assert ledger_path(home).exists() is False
    assert inactive_data.exists() is False
    assert home_data.exists() is False


def test_nested_cwd_uses_env_root_and_active_config_latches(tmp_path: Path) -> None:
    # Given: an enabled project whose hook cwd is a nested directory.
    root = tmp_path / "project"
    nested = root / "src" / "feature"
    nested.mkdir(parents=True)
    config = write_config(root)
    data_dir = tmp_path / "plugin-data"
    session_id = "root-latch"
    harness = HookHarness(nested, root, data_dir)

    # When: a normal prompt starts, then config is disabled and env disappears.
    prompt = _prompt(nested, session_id, "app.py 파일을 수정해줘")
    prompt["agent_id"] = "worker-a"
    prompt_result = harness.run("user_prompt_submit.py", prompt)
    config.write_text('{"schema_version": 1, "supervision": false}', encoding="utf-8")
    deny_result = HookHarness(nested, None, data_dir).run(
        "pre_tool_use.py",
        {
            "cwd": str(nested),
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "migrations/001.sql",
                "new_string": "DROP TABLE users;",
            },
            "tool_use_id": "danger",
            "session_id": session_id,
            "prompt_id": "prompt-root-latch",
            "agent_id": "worker-a",
        },
    )

    # Then: only the canonical root has state and denial uses supported schema.
    assert "hookSpecificOutput" in prompt_result.output
    assert ledger_path(root).exists()
    assert ledger_path(nested).exists() is False
    registry = read_json(registry_path(data_dir, session_id))
    assert registry["root"] == str(root.resolve())
    assert "decision" not in deny_result.output
    deny = deny_result.output["hookSpecificOutput"]
    assert isinstance(deny, dict)
    assert deny["hookEventName"] == "PreToolUse"
    assert deny["permissionDecision"] == "deny"


def test_common_project_root_accepts_the_latched_ancestor(tmp_path: Path) -> None:
    # Given: the canonical project root is an ancestor of the hook cwd.
    root = tmp_path / "project"
    nested = root / "src" / "feature"
    nested.mkdir(parents=True)

    # When: shared Claude adapter helpers resolve the already-latched root.
    resolved = project_root({"cwd": str(nested), "project_root": str(root)})

    # Then: the canonical ancestor wins instead of the nested cwd.
    assert Path(resolved) == root.resolve()


def test_fail_open_health_warning_is_emitted_once_per_session(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: one active session encounters the same fail-open path twice.
    context = HookContext(
        active=True,
        payload={},
        root=tmp_path,
        data_dir=tmp_path / "plugin-data",
        session_id="fail-open-once",
        agent="claude",
        task_mode="normal",
        turn_prompt="",
        turn_prompt_id="",
        warning="",
    )

    # When: the adapter reports both failures.
    assert fail_open("boom", context) == 0
    first = json.loads(capsys.readouterr().out)
    assert fail_open("boom again", context) == 0
    second = json.loads(capsys.readouterr().out)

    # Then: only the first failure is visible for the session.
    assert str(first["systemMessage"]).startswith("[smtw] health: fail-open")
    assert second == {}


def test_corrupt_ledger_warns_once_and_is_recovered(tmp_path: Path) -> None:
    # Given: an enabled project has a malformed ledger before a normal prompt.
    root = tmp_path / "project"
    root.mkdir()
    write_config(root)
    ledger = ledger_path(root)
    ledger.write_text("{broken", encoding="utf-8")
    harness = HookHarness(root, root, tmp_path / "plugin-data")
    session_id = "ledger-corrupt"

    # When: two prompts run in the same session.
    first = harness.run(
        "user_prompt_submit.py",
        _prompt(root, session_id, "app.py 파일을 수정해줘"),
    )
    second_prompt = _prompt(root, session_id, "app.py 파일을 다시 수정해줘")
    second_prompt["prompt_id"] = "prompt-ledger-corrupt-2"
    second = harness.run("user_prompt_submit.py", second_prompt)

    # Then: recovery is visible once and the corrupt bytes are preserved.
    assert str(first.output.get("systemMessage", "")).startswith("[smtw] health:")
    assert "systemMessage" not in second.output
    assert list(ledger.parent.glob("ledger.json.corrupt-*.bak"))


def test_stop_prefers_last_assistant_message_over_transcript(tmp_path: Path) -> None:
    # Given: Stop carries current assistant text and an older transcript fallback.
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": "old"},
            }
        ),
        encoding="utf-8",
    )

    # When: the Claude adapter resolves completion text.
    result = transcript_last_assistant_text(
        {
            "last_assistant_message": "current",
            "transcript_path": str(transcript),
        }
    )

    # Then: the official Stop field wins.
    assert result == "current"
