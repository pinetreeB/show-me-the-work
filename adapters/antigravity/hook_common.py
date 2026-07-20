from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from core.adapter_observation import CanonicalInvocation
    from core.classify import JsonObject as Classification
    from core.ledger import JsonObject

def mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def mapping_sequence(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [cast(Mapping[str, object], item) for item in value if isinstance(item, Mapping)]


def string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value if isinstance(item, str) and item]


def project_root(payload: Mapping[str, object]) -> str:
    workspaces = string_list(payload.get("workspacePaths"))
    cwd = workspaces[0] if workspaces else payload.get("cwd") or os.getcwd()
    return str(cwd)


def canonical_invocation(
    payload: Mapping[str, object],
    phase: str,
    family: str,
    paths: list[str],
    command: str,
    success: bool,
    evidence: str,
) -> CanonicalInvocation:
    from core.adapter_observation import CanonicalInvocation

    session = payload.get("conversationId") or payload.get("session_id")
    identity_synthetic = not isinstance(session, str) or not session
    session_id = session if isinstance(session, str) and session else "default"
    agent_value = payload.get("agent")
    agent = agent_value if isinstance(agent_value, str) and agent_value else "antigravity"
    step_value = payload.get("stepIdx")
    step_id = str(step_value) if isinstance(step_value, int | str) and not isinstance(step_value, bool) else ""
    turn_value = payload.get("turn_id")
    turn_is_synthetic = not isinstance(turn_value, str) or not turn_value
    turn_id = turn_value if not turn_is_synthetic else ":".join(filter(None, ("turn", session_id, step_id)))
    invocation_value = payload.get("tool_use_id") or payload.get("invocation_id") or payload.get("tool_call_id")
    invocation_id = invocation_value if isinstance(invocation_value, str) and invocation_value else ":".join(filter(None, ("tool", session_id, step_id, family)))
    return CanonicalInvocation(
        "antigravity",
        agent,
        session_id,
        turn_id,
        invocation_id,
        phase,
        family,
        tuple(sorted({path.replace("\\", "/") for path in paths if path})),
        command,
        success,
        evidence,
        identity_synthetic,
        turn_is_synthetic,
    )


def packs_with_intent(packs_value: object, intent_required: bool) -> list[str]:
    packs = string_list(packs_value)
    if intent_required and "intent-interview" not in packs:
        packs.append("intent-interview")
    return packs


def append_intent_context(lines: list[str], intent_required: bool, intent_command: str) -> None:
    if not intent_required:
        return
    lines.extend([
        "의도 확인 필요: 수정 전 `확인질문 N:` 형식으로 목표/범위/비목표를 최대 3개만 물어보세요.",
        f"확인되면 정확히 이 명령을 그대로 실행하세요: `{intent_command}`",
        "저장소 루트에서 직접 실행 중이면 `python -m fable_lite intent set ...`도 가능하지만, 플러그인 사용 중에는 위 절대경로 명령을 우선하세요.",
        "사용자가 묻지 말라고 한 경우에만 합리적 가정을 기록하고 명령 끝에 `--assumed`를 붙이세요.",
    ])


def prepare_turn(
    payload: Mapping[str, object],
    fallback_prompt: str,
    intent_script: str,
    classifier: Callable[[Mapping[str, object]], Classification],
    *,
    force: bool = False,
) -> list[str]:
    from adapters.intent_command import intent_set_command
    from adapters.antigravity.turn_prompt import user_prompt
    from core.adapter_observation import start_turn
    from core.ambiguity import evaluate_ambiguity
    from core.intent import clear_intent
    from core.ledger import load_ledger, record_event
    from core.provenance_types import ProvenanceStatus
    from core.verification_covers import active_turn

    root = project_root(payload)
    prompt = user_prompt(payload, fallback_prompt)
    invocation = canonical_invocation(payload, "turn_start", "other", [], "", True, "")
    ledger_payload = {
        "host": invocation.host,
        "agent": invocation.agent,
        "session_id": invocation.session_id,
    }
    turn = active_turn(load_ledger({"project_root": root}), ledger_payload)
    result = classifier({"project_root": root, "prompt": prompt})
    upgrading_design = (
        turn is not None
        and result.get("design_required") is True
        and turn.get("design_required") is not True
        and not turn.get("changed_files_seen")
    )
    if turn is not None and not upgrading_design and not force:
        return _context_lines(result, False, intent_set_command(intent_script))

    if turn is None or force:
        observation = start_turn(Path(root), invocation)
        baseline_snapshot_id = observation.baseline_snapshot_id
        current_snapshot_id = observation.snapshot_id
        incomplete = observation.incomplete
        status = observation.status.value
        status_reason = observation.status_reason
    else:
        baseline_snapshot_id = _string(turn.get("baseline_snapshot_id"))
        current_snapshot_id = _string(turn.get("current_snapshot_id"))
        incomplete = turn.get("provenance_incomplete") is True
        status = _string(turn.get("provenance_status")) or ProvenanceStatus.COMPLETE.value
        status_reason = _string(turn.get("provenance_status_reason"))

    _ = clear_intent(root)
    ambiguity = evaluate_ambiguity({
        "project_root": root,
        "prompt": prompt,
        "requested_paths": string_list(result.get("requested_paths")),
    })
    intent_required = ambiguity.get("ambiguous") is True
    command_template = intent_set_command(intent_script)
    packs = packs_with_intent(result.get("packs", []), intent_required)
    _ = record_event({
        "project_root": root,
        "event": "prompt",
        "host": invocation.host,
        "agent": invocation.agent,
        "session_id": invocation.session_id,
        "turn_id": invocation.turn_id,
        "baseline_snapshot_id": baseline_snapshot_id,
        "current_snapshot_id": current_snapshot_id,
        "provenance_incomplete": incomplete,
        "provenance_status": status,
        "provenance_status_reason": status_reason,
        "task_mode": result.get("mode", "quick"),
        "prompt": prompt,
        "packs": packs,
        "needs_goals": result.get("needs_goals", False),
        "intent_required": intent_required,
        "ambiguity_score": ambiguity.get("ambiguity_score")
        if isinstance(ambiguity.get("ambiguity_score"), int)
        else 0,
        "requires_investigation_compliance": "investigation" in packs,
    })
    return _context_lines(result, intent_required, command_template)


def _context_lines(
    result: Mapping[str, object], intent_required: bool, intent_command: str
) -> list[str]:
    lines = [
        "show-me-the-work 활성화: 작업 규율을 절차로 적용하세요.",
        f"mode={result.get('mode', 'quick')}",
    ]
    packs = string_list(result.get("packs"))
    if "investigation" in packs:
        lines.extend([
            "조사 팩 준수 필수: 출력에 `가설 1:`, `가설 2:`, `가설 3:`, `기각:`, `증거:`를 포함하세요.",
            "수정 전 재현과 경쟁 가설을 먼저 기록하세요.",
        ])
    if "verification-grounding" in packs:
        lines.append("렌더/실행 산출물은 RUN→OBSERVE→FIX→RE-RUN 증거 없이는 완료하지 마세요.")
    if result.get("needs_goals") is True:
        lines.append("2+ 스토리 작업입니다. goals 체크포인트를 만들거나 사용자에게 명시 확인을 받으세요.")
    append_intent_context(lines, intent_required, intent_command)
    return lines


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def emit(payload: Mapping[str, object]) -> int:
    data = json.dumps(payload, ensure_ascii=False)
    _ = sys.stdout.buffer.write(data.encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def stop_context(
    payload: Mapping[str, object],
) -> tuple[str, CanonicalInvocation, JsonObject]:
    from core.adapter_observation import finish_turn, resolve_active_invocation

    root = project_root(payload)
    invocation = canonical_invocation(payload, "stop", "other", [], "", True, "")
    invocation = resolve_active_invocation(Path(root), invocation)
    _ = finish_turn(Path(root), invocation)
    assistant_text = ""
    request = mapping(payload.get("llm_request"))
    for message in reversed(mapping_sequence(request.get("messages"))):
        if message.get("role") in ["assistant", "model"]:
            assistant_text = str(message.get("content", ""))
            break
    return root, invocation, {
        "project_root": root,
        "stop_hook_active": False,
        "assistant_text": assistant_text,
        "host": invocation.host,
        "agent": invocation.agent,
        "session_id": invocation.session_id,
        "turn_id": invocation.turn_id,
        "attribution": invocation.scorecard_attribution,
    }


def emit_stop_result(
    root: str, invocation: CanonicalInvocation, result: Mapping[str, object]
) -> int:
    from core.adapter_observation import restart_blocked_turn

    if result.get("decision") == "block":
        restart_blocked_turn(Path(root), invocation)
        return emit({"decision": "continue", "reason": str(result.get("reason", ""))})
    return emit({})


def handle_after_agent(payload: Mapping[str, object]) -> int:
    from core.verify_state import evaluate_stop

    root, invocation, stop_payload = stop_context(payload)
    return emit_stop_result(root, invocation, evaluate_stop(stop_payload))
