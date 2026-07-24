"""show-me-the-work single dispatcher for qwen-code hooks.

Usage: qwen_hook.py <EventName>  (stdin: qwen hook JSON payload)

antigravity/oma_hook.py의 단일 디스패처 패턴 + codex_cli 4개 훅 파일의
payload/게이트 로직 이식. 출력 규약은 qwen-code 0.20.1 실증 기반
(tmp/qwen-adapter-smoke.md):

- PreToolUse/R2 차단: exit 2 + stderr 사유 (stdout deny JSON 병행).
- Stop 차단: exit 0 + {"decision":"block","reason":...}.
- fail-open: exit 0 + {"decision":"allow",...} (훅 오류로 작업을 막지 않음).
- fail-closed: SmtwEnvConflictError만 환경 충돌로 차단(fail-closed).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import sys
from pathlib import Path
from typing import TypeGuard

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 주의: core.* import는 모듈 최상단에 두지 않는다 — main()의 try 블록 밖에서
# 실행되면 core에 문제가 생겼을 때 fail-open 없이 훅 전체가 죽는다.
# 각 handle_* 함수 안에서 필요한 것만 지역 import한다(antigravity/oma_hook.py 지침).


def emit(payload: Mapping[str, object]) -> int:
    data = json.dumps(dict(payload), ensure_ascii=False)
    _ = sys.stdout.buffer.write(data.encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def emit_deny(reason: str) -> int:
    """PreToolUse/R2 차단 규약: exit 2 + stderr 사유(qwen 실증 차단 경로).

    qwen은 exit 2일 때 stderr만 파싱한다. stdout deny JSON은 방어용 병행 출력이다.
    """
    _ = sys.stderr.write(reason)
    data = json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False)
    _ = sys.stdout.buffer.write(data.encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 2


def fail_open(message: str) -> int:
    return emit(
        {
            "decision": "allow",
            "reason": f"[smtw] fail-open: {message}",
            "systemMessage": f"[smtw] fail-open: {message}",
        }
    )


def fail_closed_runtime_env(event_name: str, error: BaseException) -> int | None:
    error_type = type(error)
    if (
        error_type.__module__ != "core.runtime_env"
        or error_type.__name__ != "SmtwEnvConflictError"
    ):
        return None
    from core.runtime_env import SmtwEnvConflictError

    if not isinstance(error, SmtwEnvConflictError):
        return None
    reason = f"[smtw] runtime environment conflict; denied fail-closed: {error}"
    if event_name == "Stop":
        return emit({"decision": "block", "reason": reason})
    return emit_deny(reason)


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


def _score(value: object) -> int:
    return value if isinstance(value, int) else 0


def _packs_with_intent(packs_value: object, intent_required: bool) -> list[str]:
    packs = _string_list(packs_value)
    if intent_required and "intent-interview" not in packs:
        packs.append("intent-interview")
    return packs


def _prompt_context(result: Mapping[str, object], ambiguity: Mapping[str, object], intent_command: str) -> str:
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
        lines.append("렌더/실행 산출물은 RUN→OBSERVE→FIX→RE-RUN 증거 없이는 완료하지 마세요.")
    if needs_goals:
        lines.append("2+ 스토리 작업입니다. goals 체크포인트를 만들거나 사용자에게 명시 확인을 받으세요.")
    if intent_required:
        lines.extend(
            [
                "의도 확인 필요: 수정 전 `확인질문 N:` 형식으로 목표/범위/비목표를 최대 3개만 물어보세요.",
                f"확인되면 정확히 이 명령을 그대로 실행하세요: `{intent_command}`",
                "저장소 루트에서 직접 실행 중이면 `python -m smtw intent set ...`도 가능하지만, 플러그인 사용 중에는 위 절대경로 명령을 우선하세요.",
                "사용자가 묻지 말라고 한 경우에만 합리적 가정을 기록하고 명령 끝에 `--assumed`를 붙이세요.",
            ]
        )
    return "\n".join(lines)


def handle_user_prompt_submit(payload: Mapping[str, object]) -> int:
    from adapters.intent_command import intent_set_command
    from adapters.qwen_code.common import canonical_invocation, project_root
    from core.adapter_observation import start_turn
    from core.ambiguity import evaluate_ambiguity
    from core.classify import classify_prompt
    from core.intent import clear_intent
    from core.ledger import record_event

    root_value = project_root(payload)
    invocation = canonical_invocation(payload, "turn_start", "other", [], "", True, "")
    observation = start_turn(Path(root_value), invocation)
    _ = clear_intent(root_value)
    prompt_value = payload.get("prompt")
    prompt = prompt_value if isinstance(prompt_value, str) else ""
    result = classify_prompt({"prompt": prompt})
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
            "task_mode": result["mode"],
            "prompt": prompt,
            "packs": packs,
            "needs_goals": result["needs_goals"],
            "intent_required": intent_required,
            "ambiguity_score": _score(ambiguity.get("ambiguity_score")),
            "requires_investigation_compliance": requires_compliance,
        }
    )
    return emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": _prompt_context({**result, "packs": packs}, ambiguity, command_template),
            }
        }
    )


def handle_pre_tool_use(payload: Mapping[str, object]) -> int:
    from adapters.intent_command import intent_set_command
    from adapters.qwen_code.common import (
        canonical_invocation,
        canonical_tool_name,
        project_root,
        tool_command,
        tool_file_paths,
        tool_input,
    )
    from core.contract import EDIT_TOOLS, SHELL_TOOLS, evaluate_pretool_contract
    from core.destructive_guard import evaluate_r2_destructive_gate

    tool_name = canonical_tool_name(payload)
    family = "edit" if tool_name in EDIT_TOOLS else "shell" if tool_name in SHELL_TOOLS else "other"
    root = project_root(payload)
    invocation = canonical_invocation(
        payload,
        "pre_tool",
        family,
        tool_file_paths(payload),
        tool_command(payload),
        False,
        "",
    )
    # R2 first: resolve_active_invocation 등 다른 원장 읽기보다 먼저 실행.
    # 여기서의 예외는 destructive_guard 안에서 "degraded"로 흡수되므로
    # 뒤의 광역 fail-open이 이 결정을 되돌릴 수 없다.
    r2_result = evaluate_r2_destructive_gate(
        {
            "project_root": root,
            "tool_name": "Bash" if family == "shell" else tool_name,
            "command": tool_command(payload),
            "host": invocation.host,
            "agent": invocation.agent,
            "session_id": invocation.session_id,
        }
    )
    if r2_result["decision"] == "block":
        from core.adapter_observation import record_r2_deny_after_resolution

        _ = record_r2_deny_after_resolution(
            Path(root),
            invocation,
            str(r2_result.get("coordination_reason_code", "")),
        )
        return emit_deny(str(r2_result["reason"]))

    from core.adapter_observation import begin_invocation, resolve_active_invocation

    invocation = resolve_active_invocation(Path(root), invocation)
    # QWEN-01(codex CODEX-01 이식): 복원된 invocation(session_id 생략 → 유일한
    # 활성 턴으로 해석)가 실제 session_id를 가진 뒤에도 attribution=legacy_default에
    # 머물면 안 된다. 그렇지 않으면 복원된 신원의 유효한 namespaced contract가
    # 미귀속 편집으로 잘못 취급된다.
    attribution = invocation.scorecard_attribution
    if attribution == "legacy_default" and invocation.session_id != "default":
        attribution = "exact"
    input_text = json.dumps(tool_input(payload), ensure_ascii=False)
    result = evaluate_pretool_contract(
        {
            "project_root": project_root(payload),
            "tool_name": tool_name,
            "file_paths": tool_file_paths(payload),
            "command": tool_command(payload),
            "prompt": input_text,
            "intent_set_command": intent_set_command(__file__),
            "host": invocation.host,
            "agent": invocation.agent,
            "session_id": invocation.session_id,
            "turn_id": invocation.turn_id,
            "attribution": attribution,
        }
    )
    if result["decision"] == "block":
        return emit_deny(str(result["reason"]))
    observation = begin_invocation(Path(project_root(payload)), invocation)
    if observation.error_kind == "StaleTurn" and invocation.mutation_capable:
        detail = " identity conflict" if invocation.identity_conflict else ""
        return emit_deny(f"[smtw] stale turn{detail}; submit a current prompt before mutation.")
    return emit({})


def handle_post_tool_use(payload: Mapping[str, object]) -> int:
    from adapters.qwen_code.common import (
        canonical_invocation,
        canonical_tool_name,
        project_root,
        tool_command,
        tool_file_paths,
        tool_output,
        tool_success,
    )
    from core.adapter_observation import observe_post_tool, resolve_active_invocation, verification_covers
    from core.classify import classify_prompt
    from core.contract import EDIT_TOOLS, SHELL_TOOLS, record_contract_authored_event
    from core.ledger import JsonObject, load_ledger, record_event_if_current_turn
    from core.provenance_types import ProvenanceStatus
    from core.scope_guard import evaluate_scope
    from core.verification import is_verification_command

    root = project_root(payload)
    tool_name = canonical_tool_name(payload)
    family = "edit" if tool_name in EDIT_TOOLS else "shell" if tool_name in SHELL_TOOLS else "other"
    if family == "other":
        return emit({})
    command = tool_command(payload)
    invocation = canonical_invocation(
        payload,
        "post_tool",
        family,
        tool_file_paths(payload),
        command,
        tool_success(payload),
        tool_output(payload),
    )
    invocation = resolve_active_invocation(Path(root), invocation)
    # QWEN-02(codex CODEX-02 이식): 복원된 신원의 attribution을 contract 저자
    # 기록 전에 승격한다. 그렇지 않으면 복원된 신원의 namespaced-contract 편집이
    # attribution=legacy_default로 기록돼 이후 exact-identity 고위험 편집의
    # 상호 점검이 이뤄지지 않는다.
    attribution = invocation.scorecard_attribution
    if attribution == "legacy_default" and invocation.session_id != "default":
        attribution = "exact"
    if family == "edit" and not invocation.identity_conflict:
        record_contract_authored_event(
            {
                "project_root": root,
                "file_paths": tool_file_paths(payload),
                "host": invocation.host,
                "agent": invocation.agent,
                "session_id": invocation.session_id,
                "turn_id": invocation.turn_id,
                "attribution": attribution,
            }
        )
    observation = observe_post_tool(Path(root), invocation)
    verification_command = family == "shell" and is_verification_command(command)
    if observation.error_kind == "StaleTurn":
        return emit({"systemMessage": "[smtw] provenance incomplete; fail-open observation."})
    if verification_command:
        covers = verification_covers(Path(root), invocation)
        verification: JsonObject = {
            "project_root": root,
            "event": "verification",
            "host": invocation.host,
            "agent": invocation.agent,
            "session_id": invocation.session_id,
            "turn_id": invocation.turn_id,
            "invocation_id": invocation.invocation_id,
            "command": command,
            "success": invocation.success,
            "evidence": invocation.evidence,
        }
        if covers is not None:
            verification["covers"] = covers
        _ = record_event_if_current_turn(verification, allow_missing=True)
        return emit({"systemMessage": "[smtw] 원장: 검증 기록 / recorded verification."})
    if observation.status is ProvenanceStatus.SCOPE_TOO_LARGE:
        return emit({})
    if observation.incomplete:
        return emit({"systemMessage": "[smtw] provenance incomplete; fail-open observation."})
    paths = list(observation.changed_paths)
    ledger = load_ledger({"project_root": root})
    prompt = ledger.get("prompt")
    prompt_text = prompt if isinstance(prompt, str) else ""
    requested = classify_prompt({"prompt": prompt_text})["requested_paths"] if prompt_text else []
    scope = evaluate_scope(
        {
            "project_root": root,
            "prompt": prompt_text,
            "requested_paths": requested,
            "changed_files": paths,
        }
    )
    if scope["decision"] == "warn":
        _ = record_event_if_current_turn(
            {
                "project_root": root,
                "event": "scope_warning",
                "host": invocation.host,
                "agent": invocation.agent,
                "session_id": invocation.session_id,
                "turn_id": invocation.turn_id,
                "message": scope["message"],
            },
            allow_missing=True,
        )
        return emit(
            {
                "systemMessage": str(scope["message"]),
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": str(scope["message"]),
                },
            }
        )
    return emit({"systemMessage": f"[smtw] provenance: observed {len(paths)} change(s)."})


def handle_stop(payload: Mapping[str, object]) -> int:
    # codex_cli/stop.py 이식 — 단, codex 전용 process reaper는 제거(qwen 미해당).
    from adapters.qwen_code.common import canonical_invocation, last_assistant_text, project_root
    from core.adapter_observation import finish_turn, resolve_active_invocation, restart_blocked_turn
    from core.verify_state import evaluate_stop

    project_root_value = project_root(payload)
    invocation = canonical_invocation(payload, "stop", "other", [], "", True, "")
    invocation = resolve_active_invocation(Path(project_root_value), invocation)
    _ = finish_turn(Path(project_root_value), invocation)
    stop_payload = {
        "project_root": project_root_value,
        "stop_hook_active": payload.get("stop_hook_active") is True,
        "assistant_text": last_assistant_text(payload),
        "host": invocation.host,
        "agent": invocation.agent,
        "session_id": invocation.session_id,
        "turn_id": invocation.turn_id,
        "attribution": invocation.scorecard_attribution,
    }
    result = evaluate_stop(stop_payload)
    if result["decision"] == "block":
        restart_blocked_turn(Path(project_root_value), invocation)
        # Stop 차단 규약: exit 0 + {"decision":"block"} (qwen은 Stop에서
        # decision block을 연속 진행 사유로 처리 — 실증/소스 확인).
        return emit({"decision": "block", "reason": str(result["reason"])})
    message = str(result.get("message", "[smtw] Stop gate allow."))
    return emit({"systemMessage": message})


def handle_session_start(payload: Mapping[str, object]) -> int:
    # 턴 setup은 UserPromptSubmit가 담당한다(프롬프트 텍스트가 필요).
    # SessionStart는 안전 no-op — 확장이 필요하면 여기서 추가한다.
    return emit({})


def handle_session_end(payload: Mapping[str, object]) -> int:
    return emit({})


HANDLERS = {
    "UserPromptSubmit": handle_user_prompt_submit,
    "PreToolUse": handle_pre_tool_use,
    "PostToolUse": handle_post_tool_use,
    "Stop": handle_stop,
    "SessionStart": handle_session_start,
    "SessionEnd": handle_session_end,
}


def main() -> int:
    event_name = sys.argv[1] if len(sys.argv) >= 2 else ""
    try:
        handler = HANDLERS.get(event_name)
        if handler is None:
            return emit({})
        from adapters.qwen_code.common import read_payload

        payload = read_payload()
        return handler(payload)
    except Exception as exc:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        denied = fail_closed_runtime_env(event_name, exc)
        if denied is not None:
            return denied
        return fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
