from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias

from .agent_log import ledger_transaction
from .design_gate import is_ui_path
from .design_lint import DesignLintBaseline, DesignLintResult
from .ledger import JsonObject, JsonValue, load_ledger, save_ledger
from .ledger_v1 import sequence_value
from .ledger_v2 import refresh_v1_projection
from .verification_covers import active_turn


Decision: TypeAlias = dict[str, JsonValue]
MAX_DESIGN_BLOCKS: Final = 2


@dataclass(frozen=True, slots=True)
class DesignLintScope:
    paths: tuple[str, ...]
    baseline: DesignLintBaseline
    active: bool
    change_seq: int
    turn_key: str | None


def design_lint_scope(root: Path, agent: str) -> DesignLintScope:
    scopes = design_lint_scopes(root, agent)
    return scopes[0] if scopes else DesignLintScope((), DesignLintBaseline(), False, 0, None)


def design_lint_scopes(root: Path, agent: str) -> tuple[DesignLintScope, ...]:
    ledger = load_ledger({"project_root": str(root)})
    return tuple(
        _scope_for_turn(turn, turn_key)
        for turn_key, turn in _candidate_turns(ledger, agent)
    )


def _scope_for_turn(turn: JsonObject, turn_key: str | None) -> DesignLintScope:
    changed = turn.get("changed_files_seen")
    paths = (
        tuple(path for path in changed if isinstance(path, str) and is_ui_path(path))
        if isinstance(changed, list)
        else ()
    )
    baseline = turn.get("design_baseline_revision")
    revision = baseline if isinstance(baseline, str) and baseline else "HEAD"
    change_seq = sequence_value(turn.get("design_last_change_seq"))
    return DesignLintScope(
        paths,
        DesignLintBaseline(revision, _dirty_lines(turn)),
        True,
        change_seq,
        turn_key,
    )


def record_design_result(
    root: Path,
    agent: str,
    result: DesignLintResult,
    *,
    expected_change_seq: int,
    turn_key: str | None = None,
) -> bool:
    payload: dict[str, JsonValue] = {"project_root": str(root)}
    with ledger_transaction(str(root)):
        ledger = load_ledger(payload)
        turn = _select_turn(ledger, agent, turn_key)
        if turn is None or turn.get("design_required") is not True:
            return False
        if sequence_value(turn.get("design_last_change_seq")) != expected_change_seq:
            return False
        sequence = sequence_value(ledger.get("event_seq")) + 1
        ledger["event_seq"] = sequence
        turn["design_check_passed"] = result.passed
        turn["design_check_seq"] = sequence
        turn["design_violations"] = [item.to_json() for item in result.violations]
        if turn is not ledger:
            _ = refresh_v1_projection(ledger, turn)
        return save_ledger(payload, ledger)


def evaluate_design_stop(
    ledger: JsonObject,
    payload: Mapping[str, JsonValue],
) -> Decision | None:
    turn = active_turn(ledger, payload)
    state = turn if turn is not None else ledger
    if state.get("design_required") is not True or state.get("design_touched") is not True:
        return None
    check_sequence = sequence_value(state.get("design_check_seq"))
    change_sequence = sequence_value(state.get("design_last_change_seq"))
    if state.get("design_check_passed") is True and check_sequence > change_sequence:
        return None
    blocks = sequence_value(state.get("design_blocks"))
    if blocks >= MAX_DESIGN_BLOCKS:
        return None
    state["design_blocks"] = blocks + 1
    if turn is not None:
        _ = refresh_v1_projection(ledger, turn)
    return {
        "decision": "block",
        "reason_code": "stop_design_lint_missing",
        "reason": _block_reason(state),
    }


def _block_reason(state: Mapping[str, JsonValue]) -> str:
    violations = state.get("design_violations")
    if isinstance(violations, list) and violations:
        first = violations[0]
        if isinstance(first, dict):
            path = first.get("file")
            line = first.get("line")
            rule = first.get("rule_id")
            if isinstance(path, str) and isinstance(line, int) and isinstance(rule, str):
                return f"[smtw] 디자인 규칙 위반: {path}:{line} ({rule}). `fable_lite check --design` 재실행 필요."
    return "[smtw] UI 변경인데 통과한 design_lint 결과가 없습니다. `fable_lite check --design` 실행 후 다시 완료하세요."


def _candidate_turns(
    ledger: JsonObject,
    agent: str,
) -> tuple[tuple[str | None, JsonObject], ...]:
    turns = ledger.get("active_turns")
    if not isinstance(turns, dict):
        return ((None, ledger),) if ledger.get("design_required") is True else ()
    return tuple(
        (key, turn)
        for key, turn in sorted(turns.items())
        if isinstance(turn, dict)
        and turn.get("design_required") is True
        and (not agent or turn.get("agent") == agent)
    )


def _select_turn(
    ledger: JsonObject,
    agent: str,
    turn_key: str | None,
) -> JsonObject | None:
    candidates = _candidate_turns(ledger, agent)
    if turn_key is not None:
        return next((turn for key, turn in candidates if key == turn_key), None)
    return candidates[0][1] if len(candidates) == 1 else None


def _dirty_lines(turn: JsonObject) -> tuple[tuple[str, tuple[str, ...]], ...]:
    raw = turn.get("design_dirty_baseline")
    if not isinstance(raw, dict):
        return ()
    entries: list[tuple[str, tuple[str, ...]]] = []
    for path, hashes in raw.items():
        if isinstance(hashes, list):
            entries.append((path, tuple(item for item in hashes if isinstance(item, str))))
    return tuple(entries)
