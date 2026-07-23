from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import shlex
from typing import Final, TypeAlias

from .agent_log import ledger_transaction
from .intent import has_intent
from .ledger import JsonValue, load_ledger, save_ledger, state_dir
from .ledger_v2 import refresh_v1_projection
from .scorecard import GateAction, ReasonCode, Resolution, ScorecardSchemaError
from .scorecard_store import (
    new_transition,
    record_gate_transition_locked,
    unresolved_block_ids,
)
from .verification_covers import active_turn


Decision: TypeAlias = dict[str, JsonValue]
MAX_GOALS_BLOCKS: Final = 2
MAX_INTENT_BLOCKS: Final = 2


def needs_goals_block(payload: Mapping[str, JsonValue]) -> bool:
    root = _project_root(payload)
    ledger = load_ledger(payload)
    return _gate_required(ledger, payload, "needs_goals") and not _goals_present(
        root,
        ledger,
        payload,
    )


def needs_intent_block(payload: Mapping[str, JsonValue]) -> bool:
    root = _project_root(payload)
    return _gate_required(load_ledger(payload), payload, "intent_required") and not has_intent(root)


def recover_checkpoint_gates(payload: Mapping[str, JsonValue]) -> None:
    root = _project_root(payload)
    ledger = load_ledger(payload)
    goals_present = _goals_present(root, ledger, payload)
    intent_present = has_intent(root)
    if not goals_present and not intent_present:
        return
    with ledger_transaction(root):
        ledger = load_ledger(payload)
        goals_present = _goals_present(root, ledger, payload)
        recorded = False
        if goals_present:
            recorded = _record_scorecard(
                ledger,
                payload,
                ReasonCode.PRETOOL_GOALS_MISSING,
                GateAction.RECOVER,
                Resolution.GOALS_CHECKPOINT,
            ) or recorded
        if intent_present:
            recorded = _record_scorecard(
                ledger,
                payload,
                ReasonCode.PRETOOL_INTENT_MISSING,
                GateAction.RECOVER,
                Resolution.INTENT_CHECKPOINT,
            ) or recorded
        if recorded:
            save_ledger(payload, ledger)


def block_goals_once(payload: Mapping[str, JsonValue]) -> Decision:
    root = _project_root(payload)
    # A checkpoint created after this hint can consume one capped block, preserving short RMW locking.
    with ledger_transaction(root):
        ledger = load_ledger(payload)
        goals_present = _goals_present(root, ledger, payload)
        if not _gate_required(ledger, payload, "needs_goals") or goals_present:
            if goals_present and _record_scorecard(
                ledger,
                payload,
                ReasonCode.PRETOOL_GOALS_MISSING,
                GateAction.RECOVER,
                Resolution.GOALS_CHECKPOINT,
            ):
                save_ledger(payload, ledger)
            return {"decision": "allow", "message": "goals checkpoint is present"}
        blocks = _counter_value(ledger, payload, "goals_blocks")
        if blocks >= MAX_GOALS_BLOCKS:
            _record_scorecard(
                ledger,
                payload,
                ReasonCode.PRETOOL_GOALS_MISSING,
                GateAction.CAP_ALLOW,
            )
            save_ledger(payload, ledger)
            return {
                "decision": "allow",
                "message": "goals gate max 2 blocks reached; fail-open allow",
            }
        _set_counter(ledger, payload, "goals_blocks", blocks + 1)
        _record_scorecard(
            ledger,
            payload,
            ReasonCode.PRETOOL_GOALS_MISSING,
            GateAction.BLOCK,
        )
        save_ledger(payload, ledger)
        identity = _active_turn_identity(ledger, payload)
    identity_argument = (
        identity
        if isinstance(identity, str) and _looks_exact_identity(identity)
        else "<exact-identity>"
    )
    command = " ".join(
        (
            "smtw goals plan",
            "--root",
            shlex.quote(str(Path(root).resolve())),
            "--identity",
            shlex.quote(identity_argument),
            "--goal",
            shlex.quote("<goal>"),
            "--story",
            shlex.quote("<story>"),
            "--verify-cmd",
            shlex.quote("<verification-command>"),
        )
    )
    return {
        "decision": "block",
        "reason": (
            "[smtw] N2 checkpoint required.\n\n"
            f"Run:\n{command}\n\n"
            "Replace <goal>, <story>, and <verification-command> with real "
            "values; placeholders are not completion evidence. "
            "/ Multi-story work requires an identity-aware goals checkpoint."
        ),
    }


def block_intent_once(payload: Mapping[str, JsonValue], intent_command: str) -> Decision:
    root = _project_root(payload)
    state_name = state_dir(root).name
    intent_present = has_intent(root)
    # A checkpoint created after this hint can consume one capped block, preserving short RMW locking.
    with ledger_transaction(root):
        ledger = load_ledger(payload)
        if not _gate_required(ledger, payload, "intent_required") or intent_present:
            if intent_present and _record_scorecard(
                ledger,
                payload,
                ReasonCode.PRETOOL_INTENT_MISSING,
                GateAction.RECOVER,
                Resolution.INTENT_CHECKPOINT,
            ):
                save_ledger(payload, ledger)
            return {"decision": "allow", "message": "intent checkpoint is present"}
        blocks = _counter_value(ledger, payload, "intent_blocks")
        if blocks >= MAX_INTENT_BLOCKS:
            _record_scorecard(
                ledger,
                payload,
                ReasonCode.PRETOOL_INTENT_MISSING,
                GateAction.CAP_ALLOW,
            )
            save_ledger(payload, ledger)
            return {
                "decision": "allow",
                "message": "intent gate max 2 blocks reached; fail-open allow",
            }
        _set_counter(ledger, payload, "intent_blocks", blocks + 1)
        _record_scorecard(
            ledger,
            payload,
            ReasonCode.PRETOOL_INTENT_MISSING,
            GateAction.BLOCK,
        )
        save_ledger(payload, ledger)
    return {
        "decision": "block",
        "reason": (
            f"[smtw] intent gate: 요청 의도가 모호해 수정 전 `{state_name}/intent.json` 확정이 필요합니다. "
            "`확인질문 N:` 형식으로 목표/범위/비목표를 확인한 뒤 "
            f"`{intent_command}` 명령을 그대로 실행해 기록하세요. "
            "/ Ambiguous edit intent requires intent.json first."
        ),
    }


def _counter_value(
    ledger: Mapping[str, JsonValue], payload: Mapping[str, JsonValue], field: str
) -> int:
    turn = active_turn(ledger, payload)
    state = turn if turn is not None else ledger
    value = state.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _set_counter(
    ledger: dict[str, JsonValue],
    payload: Mapping[str, JsonValue],
    field: str,
    value: int,
) -> None:
    turn = active_turn(ledger, payload)
    if turn is None:
        ledger[field] = value
        return
    turn[field] = value
    _ = refresh_v1_projection(ledger, turn)


def _project_root(payload: Mapping[str, JsonValue]) -> str:
    root = payload.get("project_root") or payload.get("cwd")
    return root if isinstance(root, str) and root else "."


def _goals_present(
    root: str,
    ledger: Mapping[str, JsonValue],
    payload: Mapping[str, JsonValue],
) -> bool:
    legacy = state_dir(root) / "goals.json"
    identity = _active_turn_identity(ledger, payload)
    if identity is None:
        return legacy.exists()

    # Lazy import avoids core.contract -> gate_counters import recursion while
    # reusing the contract namespace's safe-key + identity-hash convention.
    from .contract import _looks_exact_identity_key, namespaced_contract_path

    filename = namespaced_contract_path(root, identity).name
    if (state_dir(root) / "goals" / filename).exists():
        return True
    if not legacy.exists() or not _looks_exact_identity_key(identity):
        return legacy.exists()
    turns = ledger.get("active_turns")
    if not isinstance(turns, dict):
        return True
    return not any(
        key != identity
        and isinstance(key, str)
        and isinstance(turn, dict)
        and _looks_exact_identity_key(key)
        for key, turn in turns.items()
    )


def _active_turn_identity(
    ledger: Mapping[str, JsonValue],
    payload: Mapping[str, JsonValue],
) -> str | None:
    turn = active_turn(ledger, payload)
    turns = ledger.get("active_turns")
    if turn is None or not isinstance(turns, dict):
        return None
    return next(
        (
            key
            for key, candidate in turns.items()
            if isinstance(key, str) and candidate is turn
        ),
        None,
    )


def _looks_exact_identity(identity: str) -> bool:
    parts = identity.split(":", 2)
    return (
        len(parts) == 3
        and bool(parts[0])
        and bool(parts[1])
        and parts[1] != "default"
        and bool(parts[2])
    )


def _gate_required(
    ledger: Mapping[str, JsonValue], payload: Mapping[str, JsonValue], field: str
) -> bool:
    if not _has_turn_identity(payload):
        # Identifier-free v1 calls use the projection under the legacy single-agent assumption.
        return ledger.get(field) is True
    turn = active_turn(ledger, payload)
    return turn is not None and turn.get(field) is True


def _has_turn_identity(payload: Mapping[str, JsonValue]) -> bool:
    return any(
        isinstance(payload.get(field), str) and bool(payload.get(field))
        for field in ("agent", "session_id")
    )


def _record_scorecard(
    ledger: dict[str, JsonValue],
    payload: Mapping[str, JsonValue],
    reason_code: ReasonCode,
    action: GateAction,
    resolution: Resolution = Resolution.NONE,
) -> bool:
    resolves = (
        ()
        if action is GateAction.BLOCK
        else unresolved_block_ids(ledger, payload, reason_code)
    )
    if action is not GateAction.BLOCK and not resolves:
        return False
    try:
        transition = new_transition(
            payload,
            reason_code,
            action,
            resolves=resolves,
            resolution=resolution,
        )
        record_gate_transition_locked(ledger, payload, transition)
    except (OSError, ScorecardSchemaError):
        return False
    return True
