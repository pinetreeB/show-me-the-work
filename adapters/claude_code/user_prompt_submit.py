from __future__ import annotations

from collections.abc import Mapping, Sequence
from importlib import import_module
import json
from pathlib import Path
import re
import sys
from typing import TYPE_CHECKING, TypeGuard

if TYPE_CHECKING:
    from adapters.claude_code.bootstrap import (
        HookContext,
        JsonObject,
        bootstrap,
        emit,
        fail_open,
        health_response,
        remember_turn,
        response,
        show_context_once,
    )
else:
    _bootstrap_module = import_module(
        "adapters.claude_code.bootstrap" if __package__ else "bootstrap"
    )
    JsonObject = _bootstrap_module.JsonObject
    HookContext = _bootstrap_module.HookContext
    bootstrap = _bootstrap_module.bootstrap
    emit = _bootstrap_module.emit
    fail_open = _bootstrap_module.fail_open
    health_response = _bootstrap_module.health_response
    remember_turn = _bootstrap_module.remember_turn
    response = _bootstrap_module.response
    show_context_once = _bootstrap_module.show_context_once


def _string_list(value: object) -> list[str]:
    if not _string_sequence(value):
        return []
    return [item for item in value if item]


def _string_sequence(value: object) -> TypeGuard[Sequence[str]]:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, str | bytes)
        and all(isinstance(item, str) for item in value)
    )


def _context(
    result: Mapping[str, object], ambiguity: Mapping[str, object], intent_command: str
) -> str:
    packs = result.get("packs")
    needs_goals = result.get("needs_goals") is True
    intent_required = ambiguity.get("ambiguous") is True
    lines = [
        "show-me-the-work 활성화: 작업 규율을 절차로 적용하세요.",
        f"mode={result.get('mode', 'quick')}",
    ]
    if isinstance(packs, list) and "investigation" in packs:
        lines.extend(
            [
                "조사 팩 준수 필수: 출력에 `가설 1:`, `가설 2:`, `가설 3:`, `기각:`, `증거:`를 포함하세요.",
                "수정 전 재현과 경쟁 가설을 먼저 기록하세요.",
            ]
        )
    if isinstance(packs, list) and "verification-grounding" in packs:
        lines.append(
            "렌더/실행 산출물은 RUN→OBSERVE→FIX→RE-RUN 증거 없이는 완료하지 마세요."
        )
    if needs_goals:
        lines.append(
            "2+ 스토리 작업입니다. goals 체크포인트를 만들거나 사용자에게 명시 확인을 받으세요."
        )
    if intent_required:
        lines.extend(
            [
                "의도 확인 필요: 수정 전 `확인질문 N:` 형식으로 목표/범위/비목표를 최대 3개만 물어보세요.",
                f"확인되면 정확히 이 명령을 그대로 실행하세요: `{intent_command}`",
                "저장소 루트에서 직접 실행 중이면 `python -m fable_lite intent set ...`도 가능하지만, 플러그인 사용 중에는 위 절대경로 명령을 우선하세요.",
                "사용자가 묻지 말라고 한 경우에만 합리적 가정을 기록하고 명령 끝에 `--assumed`를 붙이세요.",
            ]
        )
    return "\n".join(lines)


def _packs_with_intent(packs_value: object, intent_required: bool) -> list[str]:
    packs = _string_list(packs_value)
    if intent_required and "intent-interview" not in packs:
        packs.append("intent-interview")
    return packs


def _score(value: object) -> int:
    return value if isinstance(value, int) else 0


def _ledger_is_corrupt(root: Path) -> bool:
    path = root / ".fable-lite" / "ledger.json"
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (json.JSONDecodeError, OSError):
        return True
    return not isinstance(raw, dict)


def _user_response(
    context: HookContext,
    body: JsonObject,
    ledger_corrupt: bool,
) -> JsonObject:
    if ledger_corrupt:
        return health_response(
            context,
            "ledger_corrupt",
            "project ledger is corrupt; supervision is continuing fail-open",
            body,
        )
    return response(context, body)


def _effective_mode(result: Mapping[str, object], prompt: str) -> str:
    value = result.get("mode")
    mode = (
        value
        if isinstance(value, str) and value in {"quick", "normal", "deep"}
        else "quick"
    )
    if mode != "quick":
        return mode
    lowered = prompt.casefold()
    korean_mutation = any(
        marker in prompt
        for marker in (
            "수정해",
            "변경해",
            "추가해",
            "삭제해",
            "구현해",
            "작성해",
            "만들어",
            "고쳐",
            "바꿔",
            "적용해",
        )
    )
    english_mutation = re.search(
        r"\b(?:edit|modify|change|add|delete|implement|write|create|fix)\b",
        lowered,
    )
    explanation = any(
        marker in lowered
        for marker in ("방법", "어떻게", "설명", "알려", "how ", "explain", "show ")
    )
    return (
        "normal"
        if (korean_mutation or english_mutation) and not explanation
        else "quick"
    )


def main() -> int:
    context: HookContext | None = None
    try:
        context = bootstrap("UserPromptSubmit")
        if not context.active:
            return emit(response(context, {}))
        if context.root is None:
            return emit(response(context, {}))
        ledger_corrupt = _ledger_is_corrupt(context.root)
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from adapters.claude_code.common import canonical_invocation
        from adapters.intent_command import intent_set_command
        from core.ambiguity import evaluate_ambiguity
        from core.classify import classify_prompt
        from core.intent import clear_intent
        from core.ledger import record_event
        from core.adapter_observation import start_turn

        payload = context.payload
        prompt_value = payload.get("prompt")
        prompt = prompt_value if isinstance(prompt_value, str) else ""
        root_value = str(context.root)
        invocation = canonical_invocation(
            payload, "turn_start", "other", [], "", True, ""
        )
        result = classify_prompt({"project_root": root_value, "prompt": prompt})
        mode = _effective_mode(result, prompt)
        observation = start_turn(context.root, invocation) if mode != "quick" else None
        if observation is not None:
            _ = clear_intent(root_value)
        command_template = intent_set_command(__file__)
        ambiguity = evaluate_ambiguity(
            {
                "project_root": root_value,
                "prompt": prompt,
                "requested_paths": _string_list(result.get("requested_paths")),
            }
        )
        intent_required = ambiguity.get("ambiguous") is True
        packs = _packs_with_intent(result.get("packs"), intent_required)
        requires_compliance = "investigation" in packs
        result_for_context = {**result, "mode": mode, "packs": packs}
        remember_turn(context, prompt, invocation.turn_id, mode)
        if mode == "quick":
            if not intent_required or not show_context_once(
                context, invocation.turn_id
            ):
                return emit(_user_response(context, {}, ledger_corrupt))
            body: JsonObject = {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": _context(
                        result_for_context,
                        ambiguity,
                        command_template,
                    ),
                }
            }
            return emit(_user_response(context, body, ledger_corrupt))

        assert observation is not None
        _ = record_event(
            {
                "project_root": root_value,
                "event": "prompt",
                "host": invocation.host,
                "agent": invocation.agent,
                "session_id": invocation.session_id,
                "turn_id": invocation.turn_id,
                "baseline_snapshot_id": observation.baseline_snapshot_id,
                "current_snapshot_id": observation.snapshot_id,
                "provenance_incomplete": observation.incomplete,
                "provenance_status": observation.status.value,
                "provenance_status_reason": observation.status_reason,
                "task_mode": mode,
                "prompt": prompt,
                "packs": packs,
                "needs_goals": result["needs_goals"],
                "intent_required": intent_required,
                "ambiguity_score": _score(ambiguity.get("ambiguity_score")),
                "requires_investigation_compliance": requires_compliance,
            }
        )
        if not show_context_once(context, invocation.turn_id):
            return emit(_user_response(context, {}, ledger_corrupt))
        body: JsonObject = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": _context(
                    result_for_context,
                    ambiguity,
                    command_template,
                ),
            }
        }
        return emit(_user_response(context, body, ledger_corrupt))
    except Exception as exc:  # noqa: BLE001
        return fail_open(str(exc), context)


if __name__ == "__main__":
    raise SystemExit(main())
