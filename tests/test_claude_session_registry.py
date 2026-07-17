from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os
from pathlib import Path
import time

from adapters.claude_code.session_registry import (
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
    assert registry["config_digest"] == sha256(config.read_bytes()).hexdigest()
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


def test_concurrent_same_session_root_is_write_once(tmp_path: Path) -> None:
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

    # Then: one canonical root wins permanently and the loser never creates ledger.
    registry = read_json(registry_path(data_dir, session_id))
    winning_root = Path(str(registry["root"]))
    assert winning_root in roots
    assert sum(ledger_path(root).exists() for root in roots) == 1
    assert sum("systemMessage" in result.output for result in results) == 1


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
