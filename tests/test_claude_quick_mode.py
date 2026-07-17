from __future__ import annotations

from pathlib import Path

import pytest

from claude_hook_support import (
    HookHarness,
    JsonObject,
    ledger_path,
    read_json,
    write_config,
)


def _prompt(root: Path, session_id: str, text: str = "현재 상태 알려줘") -> JsonObject:
    return {
        "cwd": str(root),
        "hook_event_name": "UserPromptSubmit",
        "prompt": text,
        "session_id": session_id,
        "prompt_id": f"prompt-{session_id}",
    }


def test_quick_read_only_turn_never_creates_project_state(tmp_path: Path) -> None:
    # Given: an enabled project receiving a quick status question.
    root = tmp_path / "project"
    root.mkdir()
    write_config(root)
    harness = HookHarness(root, root, tmp_path / "plugin-data")
    prompt = _prompt(root, "quick-read")

    # When: prompt, proven-read-only shell, result, and Stop all run.
    outputs = [harness.run("user_prompt_submit.py", prompt).output]
    tool: JsonObject = {
        "cwd": str(root),
        "tool_name": "Bash",
        "tool_input": {"command": "rg --no-config --files"},
        "tool_use_id": "read",
        "session_id": "quick-read",
    }
    outputs.append(harness.run("pre_tool_use.py", tool).output)
    outputs.append(
        harness.run(
            "post_tool_use.py",
            tool
            | {
                "hook_event_name": "PostToolUse",
                "tool_response": {"exit_code": 0, "stdout": "README.md"},
            },
        ).output
    )
    outputs.append(
        harness.run(
            "stop.py",
            {
                "cwd": str(root),
                "session_id": "quick-read",
                "stop_hook_active": False,
                "last_assistant_message": "현재 상태를 확인했습니다.",
            },
        ).output
    )

    # Then: every hook is quiet and neither ledger nor snapshots exist.
    assert outputs == [{}, {}, {}, {}]
    assert ledger_path(root).exists() is False
    assert (root / ".fable-lite" / "snapshots").exists() is False


@pytest.mark.parametrize(
    "tool_name, command",
    [("Edit", ""), ("Bash", "opaque-writer"), ("Bash", 'ssh host "touch marker"')],
)
def test_quick_mutation_or_unknown_promotes_before_tool(
    tmp_path: Path,
    tool_name: str,
    command: str,
) -> None:
    # Given: a quick turn with no project baseline.
    root = tmp_path / tool_name / str(len(command))
    root.mkdir(parents=True)
    write_config(root)
    harness = HookHarness(root, root, tmp_path / f"data-{tool_name}-{len(command)}")
    session_id = f"promote-{tool_name}-{len(command)}"
    _ = harness.run("user_prompt_submit.py", _prompt(root, session_id))
    assert ledger_path(root).exists() is False

    # When: a mutation-capable or unknown tool reaches PreToolUse.
    payload: JsonObject = {
        "cwd": str(root),
        "tool_name": tool_name,
        "tool_input": {"file_path": "app.py", "command": command},
        "tool_use_id": "mutation",
        "session_id": session_id,
    }
    result = harness.run("pre_tool_use.py", payload)

    # Then: promotion and baseline creation finish before tool allowance.
    assert result.output == {}
    assert ledger_path(root).exists()
    assert (root / ".fable-lite" / "snapshots").exists()


def test_failed_partial_mutation_stays_observed_and_quiet(tmp_path: Path) -> None:
    # Given: a quick turn promoted before an unknown shell command.
    root = tmp_path / "project"
    root.mkdir()
    write_config(root)
    harness = HookHarness(root, root, tmp_path / "plugin-data")
    session_id = "partial-failure"
    _ = harness.run("user_prompt_submit.py", _prompt(root, session_id))
    tool: JsonObject = {
        "cwd": str(root),
        "tool_name": "Bash",
        "tool_input": {"command": "opaque-writer"},
        "tool_use_id": "partial",
        "session_id": session_id,
    }
    _ = harness.run("pre_tool_use.py", tool)
    (root / "partial.py").write_text("changed", encoding="utf-8")

    # When: Claude reports PostToolUseFailure after the partial write.
    result = harness.run(
        "post_tool_use.py",
        tool
        | {
            "hook_event_name": "PostToolUseFailure",
            "error": "failed after write",
            "is_interrupt": False,
        },
    )

    # Then: output stays quiet while the ledger preserves the physical change.
    ledger = read_json(ledger_path(root))
    assert result.output == {}
    assert "partial.py" in ledger["changed_files_seen"]
    turns = ledger["active_turns"]
    assert isinstance(turns, dict)
    turn = next(iter(turns.values()))
    assert isinstance(turn, dict)
    assert turn["provenance_mutation_capable"] is True


def test_normal_observations_are_quiet_but_ledger_evidence_remains(
    tmp_path: Path,
) -> None:
    # Given: an enabled normal edit turn with a baseline.
    root = tmp_path / "project"
    root.mkdir()
    write_config(root)
    harness = HookHarness(root, root, tmp_path / "plugin-data")
    session_id = "quiet-normal"
    _ = harness.run(
        "user_prompt_submit.py",
        _prompt(root, session_id, "app.py 파일을 수정해줘"),
    )
    tool: JsonObject = {
        "cwd": str(root),
        "tool_name": "Edit",
        "tool_input": {"file_path": "app.py"},
        "tool_use_id": "edit",
        "session_id": session_id,
    }
    _ = harness.run("pre_tool_use.py", tool)
    (root / "app.py").write_text("changed", encoding="utf-8")

    # When: the successful edit observation is recorded.
    result = harness.run(
        "post_tool_use.py",
        tool
        | {
            "hook_event_name": "PostToolUse",
            "tool_response": {"filePath": "app.py"},
        },
    )

    # Then: the hook is silent and the ledger still owns the evidence.
    assert result.output == {}
    assert read_json(ledger_path(root))["changed_files_seen"] == ["app.py"]

    verification: JsonObject = {
        "cwd": str(root),
        "tool_name": "Bash",
        "tool_input": {"command": "python -m pytest tests/test_app.py"},
        "tool_use_id": "verify",
        "session_id": session_id,
    }
    _ = harness.run("pre_tool_use.py", verification)
    verified = harness.run(
        "post_tool_use.py",
        verification
        | {
            "hook_event_name": "PostToolUse",
            "tool_response": {"exit_code": 0, "stdout": "1 passed"},
        },
    )
    assert verified.output == {}
    assert read_json(ledger_path(root))["verification_results"]


def test_scope_warning_is_context_only_and_emitted_once_per_turn(
    tmp_path: Path,
) -> None:
    # Given: a normal turn requested only app.py but will mutate other.py twice.
    root = tmp_path / "project"
    root.mkdir()
    write_config(root)
    harness = HookHarness(root, root, tmp_path / "plugin-data")
    session_id = "scope-once"
    _ = harness.run(
        "user_prompt_submit.py",
        _prompt(root, session_id, "app.py 파일만 수정해줘"),
    )
    tool: JsonObject = {
        "cwd": str(root),
        "tool_name": "Edit",
        "tool_input": {"file_path": "other.py"},
        "session_id": session_id,
    }

    # When: the same scope drift is observed twice in one turn.
    outputs: list[JsonObject] = []
    for index in range(2):
        payload = tool | {"tool_use_id": f"scope-{index}"}
        _ = harness.run("pre_tool_use.py", payload)
        (root / "other.py").write_text(f"changed-{index}", encoding="utf-8")
        outputs.append(
            harness.run(
                "post_tool_use.py",
                payload
                | {
                    "hook_event_name": "PostToolUse",
                    "tool_response": {"filePath": "other.py"},
                },
            ).output
        )

    # Then: only the first warning is visible and it is not duplicated.
    assert set(outputs[0]) == {"hookSpecificOutput"}
    assert outputs[1] == {}
    ledger = read_json(ledger_path(root))
    warnings = ledger["scope_warnings"]
    assert isinstance(warnings, list)
    assert len(warnings) == 1
