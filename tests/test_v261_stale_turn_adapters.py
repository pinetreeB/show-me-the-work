from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

import pytest

import core.adapter_observation as adapter_observation
import core.contract as contract
import core.destructive_guard as destructive_guard
from core.adapter_observation import CanonicalInvocation, start_turn
from core.ledger import load_ledger, record_event
from core.verification_covers import active_turn


AdapterRunner = Callable[[Path, bool], dict[str, object]]


def _prompt_payload(
    root: Path,
    invocation: CanonicalInvocation,
    baseline_snapshot_id: str,
    current_snapshot_id: str,
) -> dict[str, object]:
    return {
        "project_root": str(root),
        "event": "prompt",
        "host": invocation.host,
        "agent": invocation.agent,
        "session_id": invocation.session_id,
        "turn_id": invocation.turn_id,
        "attribution": "exact",
        "prompt": "inspect or edit app.py",
        "baseline_snapshot_id": baseline_snapshot_id,
        "current_snapshot_id": current_snapshot_id,
        "provenance_incomplete": False,
        "provenance_status": "complete",
        "provenance_status_reason": "",
    }


def _activate_turn(root: Path, invocation: CanonicalInvocation) -> None:
    observation = start_turn(root, invocation)
    assert observation.incomplete is False
    assert observation.baseline_snapshot_id
    _ = record_event(
        _prompt_payload(
            root,
            invocation,
            observation.baseline_snapshot_id,
            observation.snapshot_id,
        )
    )


def _inject_successor_before_invocation_commit(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    invocation: CanonicalInvocation,
) -> str:
    real_record = adapter_observation.record_turn_event_if_ready
    successor_turn_id = "turn-successor"
    injected = False

    def replace_turn(
        event_root: Path,
        payload: dict[str, object],
        expected_baseline_snapshot_id: str,
    ) -> object:
        nonlocal injected
        if not injected:
            injected = True
            successor = replace(invocation, turn_id=successor_turn_id)
            _activate_turn(root, successor)
        return real_record(event_root, payload, expected_baseline_snapshot_id)  # type: ignore[arg-type]

    monkeypatch.setattr(
        adapter_observation,
        "record_turn_event_if_ready",
        replace_turn,
    )
    return successor_turn_id


def _allow_unrelated_pretool_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        destructive_guard,
        "evaluate_r2_destructive_gate",
        lambda _payload: {"decision": "allow", "reason": ""},
    )
    monkeypatch.setattr(
        contract,
        "evaluate_pretool_contract",
        lambda _payload: {"decision": "allow", "reason": ""},
    )


def _run_claude(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    mutation_capable: bool,
) -> dict[str, object]:
    from adapters.claude_code.bootstrap import HookContext
    from adapters.claude_code import pre_tool_use

    payload: dict[str, object] = {
        "cwd": str(root),
        "tool_name": "Edit" if mutation_capable else "Read",
        "tool_input": {"file_path": "app.py"},
        "session_id": "session",
        "agent": "claude",
        "prompt_id": "turn-original",
        "tool_use_id": "mutation" if mutation_capable else "read-only",
    }
    context = HookContext(
        True,
        payload,  # type: ignore[arg-type]
        root,
        root / ".claude-test-data",
        "session",
        "claude",
        "normal",
        "inspect or edit app.py",
        "turn-original",
        "",
    )
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(pre_tool_use, "bootstrap", lambda _event: context)
    monkeypatch.setattr(
        pre_tool_use,
        "emit",
        lambda output: emitted.append(dict(output)) or 0,
    )

    assert pre_tool_use.main() == 0
    assert len(emitted) == 1
    return emitted[0]


def _run_codex(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    mutation_capable: bool,
) -> dict[str, object]:
    from adapters.codex_cli import common, pre_tool_use

    payload: dict[str, object] = {
        "cwd": str(root),
        "tool_name": "Edit" if mutation_capable else "Read",
        "tool_input": {"file_path": "app.py"},
        "session_id": "session",
        "agent": "codex",
        "turn_id": "turn-original",
        "tool_use_id": "mutation" if mutation_capable else "read-only",
    }
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(common, "read_payload", lambda: payload)
    monkeypatch.setattr(
        common,
        "emit",
        lambda output: emitted.append(dict(output)) or 0,
    )

    assert pre_tool_use.main() == 0
    assert len(emitted) == 1
    return emitted[0]


def _run_antigravity(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    mutation_capable: bool,
) -> dict[str, object]:
    from adapters.antigravity import hook_common, oma_hook

    payload: dict[str, object] = {
        "cwd": str(root),
        "tool_name": "write_to_file" if mutation_capable else "view_file",
        "tool_input": {"path": "app.py"},
        "session_id": "session",
        "agent": "antigravity",
        "turn_id": "turn-original",
        "tool_use_id": "mutation" if mutation_capable else "read-only",
    }
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(hook_common, "prepare_turn", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        oma_hook,
        "emit",
        lambda output: emitted.append(dict(output)) or 0,
    )

    assert oma_hook.handle_pre_tool_use(payload) == 0
    assert len(emitted) == 1
    return emitted[0]


ADAPTERS: tuple[tuple[str, str, str, AdapterRunner], ...] = (
    ("claude", "claude_code", "claude", _run_claude),
    ("codex", "codex_cli", "codex", _run_codex),
    ("antigravity", "antigravity", "antigravity", _run_antigravity),
)


def _decision(adapter: str, result: dict[str, object]) -> str:
    if adapter == "claude":
        nested = result.get("hookSpecificOutput")
        if not isinstance(nested, dict):
            return "allow"
        value = nested.get("permissionDecision")
    else:
        value = result.get("decision")
    return value if isinstance(value, str) else "allow"


@pytest.mark.parametrize(("adapter", "host", "agent", "runner"), ADAPTERS)
def test_mutation_stale_turn_is_denied_without_invocation_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter: str,
    host: str,
    agent: str,
    runner: AdapterRunner,
) -> None:
    (tmp_path / "app.py").write_text("before\n", encoding="utf-8")
    original = CanonicalInvocation(
        host,
        agent,
        "session",
        "turn-original",
        "mutation",
        "pre_tool",
        "edit",
        ("app.py",),
        "",
        False,
        "",
    )
    _activate_turn(tmp_path, original)
    _allow_unrelated_pretool_gates(monkeypatch)
    successor_turn_id = _inject_successor_before_invocation_commit(
        monkeypatch,
        tmp_path,
        original,
    )

    result = runner(monkeypatch, tmp_path, True)

    assert _decision(adapter, result) in {"deny", "block"}, result
    ledger = load_ledger({"project_root": str(tmp_path)})
    successor = active_turn(
        ledger,
        _prompt_payload(tmp_path, replace(original, turn_id=successor_turn_id), "", ""),
    )
    assert successor is not None
    invocations = successor.get("invocations", {})
    assert not isinstance(invocations, dict) or "mutation" not in invocations


@pytest.mark.parametrize(("adapter", "host", "agent", "runner"), ADAPTERS)
def test_read_only_stale_turn_keeps_existing_allow_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter: str,
    host: str,
    agent: str,
    runner: AdapterRunner,
) -> None:
    (tmp_path / "app.py").write_text("before\n", encoding="utf-8")
    original = CanonicalInvocation(
        host,
        agent,
        "session",
        "turn-original",
        "read-only",
        "pre_tool",
        "read",
        ("app.py",),
        "",
        False,
        "",
    )
    _activate_turn(tmp_path, original)
    _allow_unrelated_pretool_gates(monkeypatch)
    _ = _inject_successor_before_invocation_commit(monkeypatch, tmp_path, original)

    result = runner(monkeypatch, tmp_path, False)

    assert _decision(adapter, result) == "allow", result
