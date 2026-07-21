from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import time

import pytest

from adapters.claude_code import session_registry
from adapters.claude_code.session_registry import (
    gc_stale,
    load_turn,
    promote_quick,
    save_turn,
)
from claude_hook_support import (
    CLAUDE_HOOKS,
    HookHarness,
    JsonObject,
    ledger_path,
    read_json,
    registry_path,
    write_config,
)
from adapters.claude_code.project_config import load_project_config


def _prompt(root: Path, session_id: str, agent_id: str = "") -> JsonObject:
    payload: JsonObject = {
        "cwd": str(root),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "app.py 파일을 수정해줘",
        "session_id": session_id,
        "prompt_id": f"prompt-{session_id}-{agent_id or 'main'}",
    }
    if agent_id:
        payload["agent_id"] = agent_id
    return payload


def test_registry_records_canonical_root_digest_and_timestamps(
    tmp_path: Path,
) -> None:
    # Given: an enabled project and official plugin-data directory.
    root = tmp_path / "project"
    root.mkdir()
    config = write_config(root)
    data_dir = tmp_path / "plugin-data"
    session_id = "registry-fields"

    # When: UserPromptSubmit latches the session.
    _ = HookHarness(root, root, data_dir).run(
        "user_prompt_submit.py",
        _prompt(root, session_id),
    )

    # Then: the SHA-256 keyed registry carries every A2 bootstrap field.
    registry = read_json(registry_path(data_dir, session_id))
    assert registry["schema_version"] == 1
    assert registry["root"] == str(root.resolve())
    assert config.is_file()
    assert registry["config_digest"] == load_project_config(root).digest
    assert isinstance(registry["created_at"], str)
    assert isinstance(registry["last_activity_at"], str)


def test_corrupt_registry_warns_once_and_reconstructs_from_env(
    tmp_path: Path,
) -> None:
    # Given: valid active config but a corrupt registry for this session.
    root = tmp_path / "project"
    root.mkdir()
    write_config(root)
    data_dir = tmp_path / "plugin-data"
    session_id = "corrupt"
    path = registry_path(data_dir, session_id)
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    harness = HookHarness(root, root, data_dir)

    # When: the same session submits two prompts.
    first = harness.run("user_prompt_submit.py", _prompt(root, session_id))
    second = harness.run("user_prompt_submit.py", _prompt(root, session_id))

    # Then: the registry is rebuilt and only the first result carries health warning.
    assert str(first.output.get("systemMessage", "")).startswith("[smtw] health:")
    assert "systemMessage" not in second.output
    assert read_json(path)["root"] == str(root.resolve())


def test_missing_registry_reconstructs_only_with_env_and_active_config(
    tmp_path: Path,
) -> None:
    # Given: an active session whose registry is subsequently deleted.
    root = tmp_path / "project"
    nested = root / "nested"
    nested.mkdir(parents=True)
    write_config(root)
    data_dir = tmp_path / "plugin-data"
    session_id = "reconstruct"
    harness = HookHarness(root, root, data_dir)
    _ = harness.run("user_prompt_submit.py", _prompt(root, session_id))
    path = registry_path(data_dir, session_id)
    path.unlink()
    dangerous: JsonObject = {
        "cwd": str(nested),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /"},
        "tool_use_id": "danger",
        "session_id": session_id,
    }

    # When: env+config reconstruct once, then an env-free loss is observed twice.
    with_env = HookHarness(nested, root, data_dir).run("pre_tool_use.py", dangerous)
    assert path.exists()
    path.unlink()
    no_env = HookHarness(nested, None, data_dir)
    first_missing = no_env.run("pre_tool_use.py", dangerous)
    second_missing = no_env.run("pre_tool_use.py", dangerous)
    missing_prompt = no_env.run(
        "user_prompt_submit.py",
        _prompt(nested, session_id),
    )

    # Then: only the proven env path rebinds; every env-free hook stays off.
    assert "hookSpecificOutput" in with_env.output
    assert path.exists() is False
    assert str(first_missing.output.get("systemMessage", "")).startswith(
        "[smtw] health:"
    )
    assert second_missing.output == {}
    assert missing_prompt.output == {}
    assert path.exists() is False

    # And: an actually new session may still use UserPromptSubmit upward fallback.
    fresh_session = "fresh-fallback"
    fresh = no_env.run(
        "user_prompt_submit.py",
        _prompt(nested, fresh_session),
    )
    assert "hookSpecificOutput" in fresh.output
    assert read_json(registry_path(data_dir, fresh_session))["root"] == str(
        root.resolve()
    )


def test_user_prompt_gc_and_session_end_remove_registry(tmp_path: Path) -> None:
    # Given: one stale registry and one new enabled session.
    root = tmp_path / "project"
    root.mkdir()
    write_config(root)
    data_dir = tmp_path / "plugin-data"
    stale = data_dir / "sessions" / "stale.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    old = time.time() - 8 * 24 * 60 * 60
    os.utime(stale, (old, old))
    session_id = "ending"
    harness = HookHarness(root, root, data_dir)

    # When: UserPromptSubmit performs GC and SessionEnd closes the live session.
    _ = harness.run("user_prompt_submit.py", _prompt(root, session_id))
    active = registry_path(data_dir, session_id)
    assert active.exists()
    ended = harness.run(
        "session_end.py",
        {
            "cwd": str(root),
            "hook_event_name": "SessionEnd",
            "reason": "clear",
            "session_id": session_id,
        },
    )

    # Then: stale and ended session state are gone and termination is a no-op.
    assert stale.exists() is False
    assert active.exists() is False
    assert ended.output == {}
    hooks = read_json(CLAUDE_HOOKS / "hooks.json")
    entries = hooks["hooks"]
    assert isinstance(entries, dict)
    assert "SessionEnd" in entries


def test_gc_stale_checks_at_most_one_bounded_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: more global warning entries than one GC batch permits.
    data_dir = tmp_path / "plugin-data"
    warnings = data_dir / "warnings"
    warnings.mkdir(parents=True)
    for index in range(session_registry.GC_MAX_ENTRIES + 5):
        (warnings / f"{index:04d}.json").write_text("{}", encoding="utf-8")
    checked: list[Path] = []
    monkeypatch.setattr(session_registry, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        session_registry,
        "_remove_if_stale",
        lambda path, _cutoff: checked.append(path),
    )

    # When: one UserPromptSubmit GC pass runs.
    gc_stale(data_dir, "current")

    # Then: it never starts work beyond the configured entry batch.
    assert len(checked) == session_registry.GC_MAX_ENTRIES


def test_gc_stale_stops_starting_work_after_time_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: pending global entries and a monotonic clock crossing the deadline.
    data_dir = tmp_path / "plugin-data"
    warnings = data_dir / "warnings"
    warnings.mkdir(parents=True)
    for index in range(3):
        (warnings / f"{index:04d}.json").write_text("{}", encoding="utf-8")
    checked: list[Path] = []
    clock = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(session_registry, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        session_registry,
        "_remove_if_stale",
        lambda path, _cutoff: checked.append(path),
    )

    # When: one entry is checked and the cooperative deadline expires.
    gc_stale(data_dir, "current")

    # Then: no second filesystem operation starts after the time budget.
    assert len(checked) == 1


def test_session_end_emits_latched_root_mismatch_warning(tmp_path: Path) -> None:
    # Given: an active session latched to one root ends with a different env root.
    latched_root = tmp_path / "latched"
    env_root = tmp_path / "from-env"
    for root in (latched_root, env_root):
        root.mkdir()
        write_config(root)
    data_dir = tmp_path / "plugin-data"
    session_id = "session-end-mismatch"
    _ = HookHarness(latched_root, latched_root, data_dir).run(
        "user_prompt_submit.py",
        _prompt(latched_root, session_id),
    )

    # When: SessionEnd bootstraps the mismatch and cleans session state.
    ended = HookHarness(env_root, env_root, data_dir).run(
        "session_end.py",
        {
            "cwd": str(env_root),
            "hook_event_name": "SessionEnd",
            "reason": "clear",
            "session_id": session_id,
        },
    )

    # Then: cleanup completes without discarding the one health warning.
    assert str(ended.output.get("systemMessage", "")).startswith("[smtw] health:")
    assert registry_path(data_dir, session_id).exists() is False


def test_concurrent_same_session_latch_stays_write_once_with_env_roots(
    tmp_path: Path,
) -> None:
    # Given: two enabled roots racing to bind the same session id.
    roots = (tmp_path / "one", tmp_path / "two")
    for root in roots:
        root.mkdir()
        write_config(root)
    data_dir = tmp_path / "plugin-data"
    session_id = "root-race"

    # When: both UserPromptSubmit hooks run concurrently.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda root: HookHarness(root, root, data_dir).run(
                    "user_prompt_submit.py",
                    _prompt(root, session_id),
                ),
                roots,
            )
        )

    # Then: one canonical root wins permanently while each explicit env root is used.
    registry = read_json(registry_path(data_dir, session_id))
    winning_root = Path(str(registry["root"]))
    assert winning_root in roots
    assert all(ledger_path(root).exists() for root in roots)
    assert sum("systemMessage" in result.output for result in results) == 1


def test_env_root_is_effective_without_rebinding_existing_latch(
    tmp_path: Path,
) -> None:
    # Given: one session is already latched to an enabled project.
    latched_root = tmp_path / "latched"
    env_root = tmp_path / "from-env"
    for root in (latched_root, env_root):
        root.mkdir()
        write_config(root)
    data_dir = tmp_path / "plugin-data"
    session_id = "env-root-precedence"
    _ = HookHarness(latched_root, latched_root, data_dir).run(
        "user_prompt_submit.py",
        _prompt(latched_root, session_id),
    )
    original = read_json(registry_path(data_dir, session_id))
    second_prompt = _prompt(env_root, session_id)
    second_prompt["prompt_id"] = "prompt-env-root"
    third_prompt = _prompt(env_root, session_id)
    third_prompt["prompt_id"] = "prompt-env-root-again"
    harness = HookHarness(env_root, env_root, data_dir)

    # When: later hooks carry a different authoritative CLAUDE_PROJECT_DIR.
    first_mismatch = harness.run("user_prompt_submit.py", second_prompt)
    repeated_mismatch = harness.run("user_prompt_submit.py", third_prompt)

    # Then: hooks use env, the registry stays write-once, and warning is once.
    assert ledger_path(env_root).exists()
    assert read_json(registry_path(data_dir, session_id))["root"] == original["root"]
    assert "systemMessage" in first_mismatch.output
    assert "systemMessage" not in repeated_mismatch.output


def test_concurrent_sessions_and_subagents_keep_identity_separate(
    tmp_path: Path,
) -> None:
    # Given: one enabled root with two sessions and two subagents sharing a session.
    root = tmp_path / "project"
    root.mkdir()
    write_config(root)
    data_dir = tmp_path / "plugin-data"
    harness = HookHarness(root, root, data_dir)

    # When: independent sessions start concurrently and subagents start separately.
    sessions = ("session-a", "session-b")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda session: harness.run(
                    "user_prompt_submit.py",
                    _prompt(root, session),
                ),
                sessions,
            )
        )
    _ = harness.run(
        "user_prompt_submit.py",
        _prompt(root, "shared-session", "worker-a"),
    )
    _ = harness.run(
        "user_prompt_submit.py",
        _prompt(root, "shared-session", "worker-b"),
    )

    # Then: root registry files are per session while ledger identity uses agent_id.
    assert all(registry_path(data_dir, session).exists() for session in sessions)
    assert registry_path(data_dir, "shared-session").exists()
    ledger = read_json(ledger_path(root))
    turns = ledger["active_turns"]
    assert isinstance(turns, dict)
    assert any(key.endswith(":worker-a") for key in turns)
    assert any(key.endswith(":worker-b") for key in turns)


def test_quick_promotion_serializes_baseline_initialization(tmp_path: Path) -> None:
    # Given: two mutation-capable tools race on the same quick turn.
    data_dir = tmp_path / "plugin-data"
    session_id = "quick-promotion-race"
    agent = "claude"
    _ = save_turn(
        data_dir,
        session_id,
        agent,
        "현재 상태 알려줘",
        "prompt-race",
        "quick",
    )
    initializers: list[int] = []

    def initialize(marker: int) -> bool:
        with promote_quick(data_dir, session_id, agent) as claimed:
            if claimed:
                initializers.append(marker)
                time.sleep(0.05)
            return claimed

    # When: both callers attempt promotion concurrently.
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(initialize, (1, 2)))

    # Then: exactly one caller owns baseline initialization before mode becomes normal.
    assert sorted(claims) == [False, True]
    assert len(initializers) == 1
    turn = load_turn(data_dir, session_id, agent)
    assert turn is not None
    assert turn.mode == "normal"
