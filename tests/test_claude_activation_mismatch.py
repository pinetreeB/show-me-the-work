from __future__ import annotations

from pathlib import Path

from claude_hook_support import (
    HookHarness,
    JsonObject,
    ledger_path,
    read_json,
    registry_path,
    write_config,
)


def _prompt(root: Path, session_id: str, prompt_id: str) -> JsonObject:
    return {
        "cwd": str(root),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "app.py 파일을 수정해줘",
        "session_id": session_id,
        "prompt_id": prompt_id,
    }


def _latch_enabled_project(
    root: Path,
    data_dir: Path,
    session_id: str,
) -> JsonObject:
    root.mkdir()
    write_config(root)
    result = HookHarness(root, root, data_dir).run(
        "user_prompt_submit.py",
        _prompt(root, session_id, f"prompt-{session_id}-a"),
    )
    assert "hookSpecificOutput" in result.output
    assert ledger_path(root).exists()
    return read_json(registry_path(data_dir, session_id))


def test_latched_enabled_project_does_not_activate_unconfigured_env_root(
    tmp_path: Path,
) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_b.mkdir()
    data_dir = tmp_path / "plugin-data"
    session_id = "inactive-mismatch"
    original = _latch_enabled_project(project_a, data_dir, session_id)

    mismatch = HookHarness(project_b, project_b, data_dir).run(
        "user_prompt_submit.py",
        _prompt(project_b, session_id, "prompt-inactive-mismatch-b"),
    )

    assert mismatch.output == {}
    assert ledger_path(project_b).exists() is False
    registry = read_json(registry_path(data_dir, session_id))
    assert registry["root"] == original["root"]
    assert registry["config_digest"] == original["config_digest"]


def test_latched_enabled_project_can_use_separately_opted_in_env_root_if_policy_allows(
    tmp_path: Path,
) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_b.mkdir()
    write_config(project_b)
    data_dir = tmp_path / "plugin-data"
    session_id = "enabled-mismatch"
    original = _latch_enabled_project(project_a, data_dir, session_id)

    mismatch = HookHarness(project_b, project_b, data_dir).run(
        "user_prompt_submit.py",
        _prompt(project_b, session_id, "prompt-enabled-mismatch-b"),
    )

    assert "hookSpecificOutput" in mismatch.output
    assert ledger_path(project_b).exists()
    registry = read_json(registry_path(data_dir, session_id))
    assert registry["root"] == original["root"]
    assert registry["config_digest"] == original["config_digest"]


def test_root_mismatch_warning_is_emitted_once(tmp_path: Path) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_b.mkdir()
    write_config(project_b)
    data_dir = tmp_path / "plugin-data"
    session_id = "mismatch-warning"
    _ = _latch_enabled_project(project_a, data_dir, session_id)
    harness = HookHarness(project_b, project_b, data_dir)

    first = harness.run(
        "user_prompt_submit.py",
        _prompt(project_b, session_id, "prompt-mismatch-warning-b1"),
    )
    second = harness.run(
        "user_prompt_submit.py",
        _prompt(project_b, session_id, "prompt-mismatch-warning-b2"),
    )

    assert "root mismatch" in str(first.output.get("systemMessage", ""))
    assert "systemMessage" not in second.output


def test_unconfigured_mismatch_root_remains_project_stateless(
    tmp_path: Path,
) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_b.mkdir()
    data_dir = tmp_path / "plugin-data"
    session_id = "stateless-mismatch"
    original = _latch_enabled_project(project_a, data_dir, session_id)
    harness = HookHarness(project_b, project_b, data_dir)

    _ = harness.run(
        "user_prompt_submit.py",
        _prompt(project_b, session_id, "prompt-stateless-mismatch-b"),
    )
    _ = harness.run(
        "pre_tool_use.py",
        {
            "cwd": str(project_b),
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "app.py"},
            "tool_use_id": "stateless-read",
            "session_id": session_id,
            "prompt_id": "prompt-stateless-mismatch-b",
        },
    )

    assert list(project_b.rglob("*")) == []
    registry = read_json(registry_path(data_dir, session_id))
    assert registry["root"] == original["root"]
    assert registry["config_digest"] == original["config_digest"]
