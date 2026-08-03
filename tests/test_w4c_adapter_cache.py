from __future__ import annotations

from collections.abc import Callable
import inspect
import json
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import patch

from adapters.antigravity import oma_hook
from adapters.claude_code import post_tool_use as claude_post_tool_use
from adapters.claude_code import pre_tool_use as claude_pre_tool_use
from adapters.claude_code import session_end as claude_session_end
from adapters.claude_code import stop as claude_stop
from adapters.claude_code import user_prompt_submit as claude_user_prompt_submit
from adapters.claude_code.bootstrap import HookContext
from adapters.qwen_code import qwen_hook
from core import ledger as ledger_module
from core.ledger_schema import JsonObject
from core.ledger_storage import ledger_path
from core.ledger_v2 import apply_v2_event, default_v2_ledger
import pytest


def _claude_context(root: Path, data_dir: Path) -> HookContext:
    return HookContext(
        active=True,
        payload={
            "cwd": str(root),
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
            "tool_use_id": "claude-cache-tool",
            "session_id": "claude-cache-session",
            "turn_id": "claude-cache-turn",
            "agent": "claude",
        },
        root=root,
        data_dir=data_dir,
        session_id="claude-cache-session",
        agent="claude",
        task_mode="normal",
        turn_prompt="inspect repository",
        turn_prompt_id="claude-cache-turn",
        warning="",
    )


def _pretool_payload(root: Path, session_id: str, agent: str) -> JsonObject:
    return {
        "cwd": str(root),
        "project_root": str(root),
        "tool_name": "Bash",
        "tool_input": {"command": "git status --short"},
        "tool_use_id": f"{agent}-cache-tool",
        "session_id": session_id,
        "turn_id": f"{agent}-cache-turn",
        "agent": agent,
    }


def _seed_active_turn(root: Path, host: str, session_id: str, agent: str) -> None:
    ledger = default_v2_ledger()
    _ = apply_v2_event(
        ledger,
        {
            "event": "prompt",
            "seq": 1,
            "host": host,
            "session_id": session_id,
            "agent": agent,
            "turn_id": f"{agent}-cache-turn",
            "prompt": "inspect repository",
        },
    )
    turns = ledger["active_turns"]
    assert isinstance(turns, dict)
    turn = turns[f"{host}:{session_id}:{agent}"]
    assert isinstance(turn, dict)
    turn["invocations"] = {
        "tool:legacy": {
            "candidate_paths": ["core/ledger.py"],
            "started_seq": 1,
            "started_at": "2020-01-01T00:00:00+00:00",
        }
    }
    destination = ledger_path(str(root))
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps(ledger), encoding="utf-8")


def _assert_one_uncached_load(callable_hook: Callable[[], int]) -> None:
    real_uncached_load = ledger_module._load_ledger_uncached
    with patch.object(
        ledger_module,
        "_load_ledger_uncached",
        wraps=real_uncached_load,
    ) as uncached_load:
        result = callable_hook()

    assert result == 0
    assert uncached_load.call_count == 1


@pytest.mark.parametrize(
    "hook_module",
    [
        claude_user_prompt_submit,
        claude_pre_tool_use,
        claude_post_tool_use,
        claude_stop,
    ],
)
def test_each_ledger_reading_claude_entrypoint_enters_cache(
    hook_module: ModuleType,
) -> None:
    source = inspect.getsource(hook_module.main)

    assert "cache_stack.enter_context(ledger_read_cache())" in source
    assert source.index("if not context.active") < source.index(
        "cache_stack.enter_context(ledger_read_cache())"
    )


def test_claude_session_end_does_not_load_ledger(tmp_path: Path) -> None:
    context = _claude_context(tmp_path, tmp_path / "claude-data")
    real_uncached_load = ledger_module._load_ledger_uncached
    with (
        patch.object(claude_session_end, "bootstrap", return_value=context),
        patch.object(claude_session_end, "cleanup_session"),
        patch.object(claude_session_end, "emit", return_value=0),
        patch.object(
            ledger_module,
            "_load_ledger_uncached",
            wraps=real_uncached_load,
        ) as uncached_load,
    ):
        result = claude_session_end.main()

    assert result == 0
    assert uncached_load.call_count == 0


def test_claude_pretool_uses_one_process_local_ledger_load(tmp_path: Path) -> None:
    _seed_active_turn(
        tmp_path,
        "claude_code",
        "claude-cache-session",
        "claude",
    )
    context = _claude_context(tmp_path, tmp_path / "claude-data")
    with (
        patch.object(claude_pre_tool_use, "bootstrap", return_value=context),
        patch.object(claude_pre_tool_use, "emit", return_value=0),
        patch.object(
            claude_pre_tool_use,
            "fail_open",
            side_effect=AssertionError("unexpected fail-open"),
        ),
    ):
        _assert_one_uncached_load(claude_pre_tool_use.main)


def test_antigravity_pretool_uses_one_process_local_ledger_load(
    tmp_path: Path,
) -> None:
    _seed_active_turn(
        tmp_path,
        "antigravity",
        "agy-cache-session",
        "antigravity",
    )
    payload = _pretool_payload(tmp_path, "agy-cache-session", "antigravity")
    with (
        patch.object(sys, "argv", [str(oma_hook.__file__), "PreToolUse"]),
        patch.object(oma_hook, "read_payload", return_value=payload),
        patch.object(oma_hook, "emit", return_value=0),
        patch.object(
            oma_hook,
            "fail_open",
            side_effect=AssertionError("unexpected fail-open"),
        ),
    ):
        _assert_one_uncached_load(oma_hook.main)


def test_qwen_pretool_uses_one_process_local_ledger_load(tmp_path: Path) -> None:
    _seed_active_turn(tmp_path, "qwen_code", "qwen-cache-session", "qwen")
    payload = _pretool_payload(tmp_path, "qwen-cache-session", "qwen")
    from adapters.qwen_code import common as qwen_common

    with (
        patch.object(sys, "argv", [str(qwen_hook.__file__), "PreToolUse"]),
        patch.object(qwen_common, "read_payload", return_value=payload),
        patch.object(qwen_hook, "emit", return_value=0),
        patch.object(
            qwen_hook,
            "fail_open",
            side_effect=AssertionError("unexpected fail-open"),
        ),
    ):
        _assert_one_uncached_load(qwen_hook.main)
