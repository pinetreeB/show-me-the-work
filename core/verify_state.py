from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

from .ledger import JsonObject, JsonValue, load_ledger, save_ledger
from .compliance import check_investigation_compliance

Decision: TypeAlias = dict[str, JsonValue]

MAX_STOP_BLOCKS = 2


def _as_str_list(value: JsonValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_result_list(value: JsonValue | None) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _positive_sequence(value: JsonValue | None) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def has_successful_verification(ledger: Mapping[str, JsonValue]) -> bool:
    results = _as_result_list(ledger.get("verification_results"))
    if _legacy_seq_less_verification(ledger):
        return any(result.get("success") is True for result in results)
    last_change_seq = _positive_sequence(ledger.get("last_change_seq"))
    if last_change_seq is None:
        return any(result.get("success") is True for result in results)
    return any(
        result.get("success") is True
        and (verification_seq := _positive_sequence(result.get("seq"))) is not None
        and verification_seq > last_change_seq
        for result in results
    )


def _legacy_seq_less_verification(ledger: Mapping[str, JsonValue]) -> bool:
    active_turns = ledger.get("active_turns")
    if not isinstance(active_turns, dict):
        return False
    return any(
        isinstance(turn, dict)
        and turn.get("migration_mode") == "legacy_turn"
        and turn.get("legacy_seq_less") is True
        for turn in active_turns.values()
    )


def _docs_only(ledger: Mapping[str, JsonValue]) -> bool:
    changed = _as_str_list(ledger.get("changed_files_seen"))
    kinds = set(_as_str_list(ledger.get("change_kinds")))
    return bool(changed) and bool(kinds) and kinds <= {"docs"}


def _assistant_text(payload: Mapping[str, JsonValue]) -> str:
    value = payload.get("assistant_text")
    return value if isinstance(value, str) else ""


def _requires_investigation_compliance(ledger: Mapping[str, JsonValue]) -> bool:
    return ledger.get("requires_investigation_compliance") is True


def _block_with_stop_counter(payload: Mapping[str, JsonValue], ledger: JsonObject, reason: str) -> Decision:
    stop_blocks_value = ledger.get("stop_blocks")
    stop_blocks = stop_blocks_value if isinstance(stop_blocks_value, int) else 0
    if stop_blocks >= MAX_STOP_BLOCKS:
        return {
            "decision": "allow",
            "message": "최대 2회 차단 후 통과합니다 / allowing after max 2 blocks.",
        }
    ledger["stop_blocks"] = stop_blocks + 1
    # v2.0 이연: 공유 원장 지원 시 stop_blocks read-modify-write도 ledger_transaction 안으로 옮긴다.
    save_ledger(payload, ledger)
    return {"decision": "block", "reason": reason}


def evaluate_stop(payload: Mapping[str, JsonValue]) -> Decision:
    # 주의: stop_hook_active를 이유로 여기서 조건 없이 allow하지 않는다 — 그러면
    # _block_with_stop_counter의 stop_blocks 카운터가 두 번째 검사에서 실행되지 못해
    # MAX_STOP_BLOCKS(2)가 사실상 도달 불가능한 코드가 된다(v1 릴리스 심사 B2 발견,
    # p5b/e1/e1b/e1c 전 실측에서 stop_blocks가 항상 정확히 1이었던 원인).
    # 무한루프 방지는 아래 _block_with_stop_counter의 stop_blocks>=MAX_STOP_BLOCKS
    # 캡 하나로 충분하다 — stop_hook_active 여부와 무관하게 최대 2회 차단 후 반드시 통과한다.
    ledger = load_ledger(payload)
    mode_value = payload.get("task_mode") or ledger.get("task_mode")
    mode = mode_value if isinstance(mode_value, str) else "quick"
    changed = bool(_as_str_list(ledger.get("changed_files_seen")))
    verified = has_successful_verification(ledger)

    # N1 마커는 파일 변경이 있는 턴에만 요구한다 — 조사 팩의 목적은 "수정 전에
    # 제대로 조사했는가"이므로, 아무것도 고치지 않은 답변 전용 턴(질문·상담)에
    # 마커를 강제하면 규율이 아니라 마찰이다 (v1.1.3, 사용자 피드백).
    if changed and _requires_investigation_compliance(ledger):
        compliance = check_investigation_compliance({"text": _assistant_text(payload)})
        if compliance["compliant"] is not True:
            return _block_with_stop_counter(
                payload,
                ledger,
                (
                    "fable-lite N1: 조사 팩 마커가 부족합니다. "
                    "`가설 1:`/`Hypothesis 1:`, `증거:`/`Evidence:`, `기각:`/`Rejected:`를 포함하세요. "
                    "단 마커는 하단에 각 1줄 기록이면 충분합니다 — 본문은 비개발자도 읽는 쉬운 설명을 먼저 쓰고, "
                    "보고 전체를 기술 문서로 만들지 마세요. "
                    "/ Investigation pack markers are required — one compact line each at the bottom; "
                    "keep the report body in plain language first."
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
