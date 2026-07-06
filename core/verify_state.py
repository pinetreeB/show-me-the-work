from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

from .ledger import JsonObject, load_ledger, save_ledger
from .compliance import check_investigation_compliance

JsonValue: TypeAlias = str | int | bool | list[str]
Decision: TypeAlias = dict[str, JsonValue]

MAX_STOP_BLOCKS = 2


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_result_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _has_successful_verification(ledger: Mapping[str, object]) -> bool:
    return any(
        result.get("success") is True
        for result in _as_result_list(ledger.get("verification_results"))
    )


def _docs_only(ledger: Mapping[str, object]) -> bool:
    changed = _as_str_list(ledger.get("changed_files_seen"))
    kinds = set(_as_str_list(ledger.get("change_kinds")))
    return bool(changed) and bool(kinds) and kinds <= {"docs"}


def _assistant_text(payload: Mapping[str, object]) -> str:
    value = payload.get("assistant_text")
    return value if isinstance(value, str) else ""


def _requires_investigation_compliance(ledger: Mapping[str, object]) -> bool:
    return ledger.get("requires_investigation_compliance") is True


def _block_with_stop_counter(payload: Mapping[str, object], ledger: JsonObject, reason: str) -> Decision:
    stop_blocks_value = ledger.get("stop_blocks")
    stop_blocks = stop_blocks_value if isinstance(stop_blocks_value, int) else 0
    if stop_blocks >= MAX_STOP_BLOCKS:
        return {
            "decision": "allow",
            "message": "최대 2회 차단 후 통과합니다 / allowing after max 2 blocks.",
        }
    ledger["stop_blocks"] = stop_blocks + 1
    save_ledger(payload, ledger)
    return {"decision": "block", "reason": reason}


def evaluate_stop(payload: Mapping[str, object]) -> Decision:
    if payload.get("stop_hook_active") is True:
        return {"decision": "allow", "message": "Stop hook loop guard: allow."}

    ledger = load_ledger(payload)
    mode_value = payload.get("task_mode") or ledger.get("task_mode")
    mode = mode_value if isinstance(mode_value, str) else "quick"
    changed = bool(_as_str_list(ledger.get("changed_files_seen")))
    verified = _has_successful_verification(ledger)

    if _requires_investigation_compliance(ledger):
        compliance = check_investigation_compliance({"text": _assistant_text(payload)})
        if compliance["compliant"] is not True:
            return _block_with_stop_counter(
                payload,
                ledger,
                (
                    "fable-lite N1: 조사 팩 마커가 부족합니다. "
                    "`가설 1:`/`Hypothesis 1:`, `증거:`/`Evidence:`, `기각:`/`Rejected:`를 포함하세요. "
                    "/ Investigation pack markers are required."
                ),
            )

    if mode == "quick" or _docs_only(ledger) or not changed or verified:
        return {"decision": "allow", "message": "fable-lite Stop gate allow."}

    return _block_with_stop_counter(
        payload,
        ledger,
        (
            "fable-lite Stop gate: 변경 파일이 있지만 성공한 검증 증거가 없습니다. "
            "가장 좁은 테스트/실행 관측을 수행하고 evidence를 기록하세요. "
            "/ Changed files require observed successful verification."
        ),
    )
