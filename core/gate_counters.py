from __future__ import annotations

from collections.abc import Mapping
from typing import Final, TypeAlias

from .agent_log import ledger_transaction
from .intent import has_intent
from .ledger import JsonObject, JsonValue, load_ledger, save_ledger, state_dir


Decision: TypeAlias = dict[str, JsonValue]
MAX_GOALS_BLOCKS: Final = 2
MAX_INTENT_BLOCKS: Final = 2


def needs_goals_block(root: str) -> bool:
    ledger = load_ledger({"project_root": root})
    return ledger.get("needs_goals") is True and not (state_dir(root) / "goals.json").exists()


def needs_intent_block(root: str) -> bool:
    ledger = load_ledger({"project_root": root})
    return ledger.get("intent_required") is True and not has_intent(root)


def block_goals_once(root: str) -> Decision:
    payload: JsonObject = {"project_root": root}
    with ledger_transaction(root):
        ledger = load_ledger(payload)
        if ledger.get("needs_goals") is not True or (state_dir(root) / "goals.json").exists():
            return {"decision": "allow", "message": "goals checkpoint is present"}
        blocks = _counter_value(ledger, "goals_blocks")
        if blocks >= MAX_GOALS_BLOCKS:
            return {
                "decision": "allow",
                "message": "goals gate max 2 blocks reached; fail-open allow",
            }
        ledger["goals_blocks"] = blocks + 1
        save_ledger(payload, ledger)
    return {
        "decision": "block",
        "reason": (
            "fable-lite N2: 2+ 스토리 작업은 `.fable-lite/goals.json` 체크포인트가 먼저 필요합니다. "
            "goals plan을 작성하거나 명시 확인 후 다시 시도하세요. "
            "/ Multi-story work requires a goals checkpoint first."
        ),
    }


def block_intent_once(root: str, intent_command: str) -> Decision:
    payload: JsonObject = {"project_root": root}
    with ledger_transaction(root):
        ledger = load_ledger(payload)
        if ledger.get("intent_required") is not True or has_intent(root):
            return {"decision": "allow", "message": "intent checkpoint is present"}
        blocks = _counter_value(ledger, "intent_blocks")
        if blocks >= MAX_INTENT_BLOCKS:
            return {
                "decision": "allow",
                "message": "intent gate max 2 blocks reached; fail-open allow",
            }
        ledger["intent_blocks"] = blocks + 1
        save_ledger(payload, ledger)
    return {
        "decision": "block",
        "reason": (
            "fable-lite intent gate: 요청 의도가 모호해 수정 전 `.fable-lite/intent.json` 확정이 필요합니다. "
            "`확인질문 N:` 형식으로 목표/범위/비목표를 확인한 뒤 "
            f"`{intent_command}` 명령을 그대로 실행해 기록하세요. "
            "/ Ambiguous edit intent requires intent.json first."
        ),
    }


def _counter_value(ledger: Mapping[str, JsonValue], field: str) -> int:
    value = ledger.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
