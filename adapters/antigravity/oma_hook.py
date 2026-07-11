from __future__ import annotations

import sys
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeGuard, cast

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 주의: core.* import는 여기(모듈 최상단)에 두지 않는다 — main()의 try 블록
# 밖에서 실행되면 core에 문제가 생겼을 때 fail-open 없이 훅 전체가 죽는다
# (v1 릴리스 심사 B1). 각 handle_* 함수 안에서 필요한 것만 지역 import한다
# — claude_code/codex_cli의 8개 훅 파일과 동일하게, import 실패도
# main()의 try/except가 잡아 fail_open으로 처리되도록 한다.

def read_payload() -> dict[str, object]:
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    try:
        raw = cast(object, json.loads(text))
    except json.JSONDecodeError as exc:
        raise ValueError("malformed JSON payload") from exc
    if not isinstance(raw, dict):
        raise ValueError("payload must be a JSON object")
    return dict(cast(Mapping[str, object], raw))

def emit(payload: Mapping[str, object]) -> int:
    data = json.dumps(payload, ensure_ascii=False)
    _ = sys.stdout.buffer.write(data.encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0

def fail_open(msg: str) -> int:
    return emit({"decision": "allow", "systemMessage": f"fable-lite fail-open: {msg}"})

def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _mapping_sequence(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [cast(Mapping[str, object], item) for item in value if isinstance(item, Mapping)]


def _string_sequence(value: object) -> TypeGuard[Sequence[str]]:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, str | bytes)
        and all(isinstance(item, str) for item in value)
    )


def _get_project_root(payload: Mapping[str, object]) -> str:
    cwd = payload.get("cwd") or os.getcwd()
    return str(cwd)

def _string_list(value: object) -> list[str]:
    if not _string_sequence(value):
        return []
    return [item for item in value if item]

def _packs_with_intent(packs_value: object, intent_required: bool) -> list[str]:
    packs = _string_list(packs_value)
    if intent_required and "intent-interview" not in packs:
        packs.append("intent-interview")
    return packs

def _append_intent_context(lines: list[str], intent_required: bool, intent_command: str) -> None:
    if not intent_required:
        return
    lines.extend([
        "의도 확인 필요: 수정 전 `확인질문 N:` 형식으로 목표/범위/비목표를 최대 3개만 물어보세요.",
        f"확인되면 정확히 이 명령을 그대로 실행하세요: `{intent_command}`",
        "저장소 루트에서 직접 실행 중이면 `python -m fable_lite intent set ...`도 가능하지만, 플러그인 사용 중에는 위 절대경로 명령을 우선하세요.",
        "사용자가 묻지 말라고 한 경우에만 합리적 가정을 기록하고 명령 끝에 `--assumed`를 붙이세요.",
    ])

def handle_before_tool(payload: Mapping[str, object]) -> int:
    from adapters.antigravity.tool_io import extract_command, extract_paths_from_input, extract_tool_info
    from adapters.intent_command import intent_set_command
    from core.contract import evaluate_pretool_contract

    tool_name, tool_input = extract_tool_info(payload)
    paths = extract_paths_from_input(tool_input)
    cmd = extract_command(tool_input)

    result = evaluate_pretool_contract({
        "project_root": _get_project_root(payload),
        "tool_name": tool_name,
        "file_paths": paths,
        "command": cmd,
        "prompt": json.dumps(tool_input, ensure_ascii=False),
        "intent_set_command": intent_set_command(__file__)
    })
    
    if result.get("decision") == "block":
        return emit({"decision": "block", "reason": str(result.get("reason", ""))})
    return emit({"decision": "allow"})

def handle_after_tool(payload: Mapping[str, object]) -> int:
    from adapters.antigravity.tool_io import (
        extract_command,
        extract_paths_from_input,
        extract_tool_info,
        verification_result,
    )

    root = _get_project_root(payload)
    tool_name, tool_input = extract_tool_info(payload)

    from core.classify import classify_prompt
    from core.contract import EDIT_TOOLS, SHELL_TOOLS
    from core.ledger import classify_change_kind, load_ledger, record_event
    from core.scope_guard import evaluate_scope
    from core.verification import is_verification_command

    if tool_name in EDIT_TOOLS:
        paths = extract_paths_from_input(tool_input)
        for path in paths:
            _ = record_event({
                "project_root": root,
                "event": "change",
                "path": path,
                "kind": classify_change_kind(path)
            })
        ledger = load_ledger({"project_root": root})
        prompt = ledger.get("prompt", "")
        if not isinstance(prompt, str):
            prompt = ""
        
        req = classify_prompt({"prompt": prompt})["requested_paths"] if prompt else []
        scope = evaluate_scope({
            "project_root": root,
            "prompt": prompt,
            "requested_paths": req,
            "changed_files": paths
        })
        if scope.get("decision") == "warn":
            _ = record_event({"project_root": root, "event": "scope_warning", "message": scope.get("message")})
            return emit({
                "decision": "allow",
                "systemMessage": str(scope.get("message")),
                "hookSpecificOutput": {"additionalContext": str(scope.get("message"))}
            })
        return emit({"decision": "allow", "systemMessage": f"fable-lite 원장: 변경 {len(paths)}건 기록."})
        
    if tool_name in SHELL_TOOLS:
        cmd = extract_command(tool_input)
        if is_verification_command(cmd):
            success, evidence = verification_result(payload)
            _ = record_event({
                "project_root": root,
                "event": "verification",
                "command": cmd,
                "success": success,
                "evidence": evidence
            })
            return emit({"decision": "allow", "systemMessage": "fable-lite 원장: 검증 기록."})
            
    return emit({"decision": "allow"})

def handle_after_agent(payload: Mapping[str, object]) -> int:
    from core.verify_state import evaluate_stop

    root = _get_project_root(payload)

    assistant_text = ""
    req = _mapping(payload.get("llm_request"))
    for msg in reversed(_mapping_sequence(req.get("messages"))):
        if msg.get("role") in ["assistant", "model"]:
            assistant_text = str(msg.get("content", ""))
            break
                
    # OmA doesn't have stop_hook_active natively, so assume False for normal evaluation
    result = evaluate_stop({
        "project_root": root,
        "stop_hook_active": False,
        "assistant_text": assistant_text
    })
    
    if result.get("decision") == "block":
        return emit({"decision": "block", "reason": str(result.get("reason", ""))})
        
    return emit({"decision": "allow", "systemMessage": str(result.get("message", "fable-lite Stop gate allow."))})

def handle_before_model(payload: Mapping[str, object]) -> int:
    from adapters.intent_command import intent_set_command
    from core.ambiguity import evaluate_ambiguity
    from core.classify import classify_prompt
    from core.intent import clear_intent
    from core.ledger import record_event

    prompt_value = payload.get("prompt", "")
    if not isinstance(prompt_value, str):
        prompt_value = ""
    
    if not prompt_value:
        req = _mapping(payload.get("llm_request"))
        for msg in reversed(_mapping_sequence(req.get("messages"))):
            if msg.get("role") == "user":
                prompt_value = str(msg.get("content", ""))
                break

    root = _get_project_root(payload)
    _ = clear_intent(root)
    result = classify_prompt({"prompt": prompt_value})
    ambiguity = evaluate_ambiguity({
        "project_root": root,
        "prompt": prompt_value,
        "requested_paths": _string_list(result.get("requested_paths")),
    })
    intent_required = ambiguity.get("ambiguous") is True
    command_template = intent_set_command(__file__)
    packs = _packs_with_intent(result.get("packs", []), intent_required)
    
    _ = record_event({
        "project_root": root,
        "event": "prompt",
        "task_mode": result.get("mode", "quick"),
        "prompt": prompt_value,
        "packs": packs,
        "needs_goals": result.get("needs_goals", False),
        "intent_required": intent_required,
        "ambiguity_score": ambiguity.get("ambiguity_score") if isinstance(ambiguity.get("ambiguity_score"), int) else 0,
        "requires_investigation_compliance": "investigation" in packs
    })
    
    lines = [
        "fable-lite 활성화: 작업 규율을 절차로 적용하세요.",
        f"mode={result.get('mode', 'quick')}"
    ]
    if "investigation" in packs:
        lines.extend([
            "조사 팩 준수 필수: 출력에 `가설 1:`, `가설 2:`, `가설 3:`, `기각:`, `증거:`를 포함하세요.",
            "수정 전 재현과 경쟁 가설을 먼저 기록하세요."
        ])
    if "verification-grounding" in packs:
        lines.append("렌더/실행 산출물은 RUN→OBSERVE→FIX→RE-RUN 증거 없이는 완료하지 마세요.")
    if result.get("needs_goals"):
        lines.append("2+ 스토리 작업입니다. goals 체크포인트를 만들거나 사용자에게 명시 확인을 받으세요.")
    _append_intent_context(lines, intent_required, command_template)
        
    return emit({
        "decision": "allow",
        "hookSpecificOutput": {
            "additionalContext": "\n".join(lines)
        }
    })

def main() -> int:
    try:
        if len(sys.argv) < 2:
            return fail_open("No event specified")
            
        event_name = sys.argv[1]
        payload = read_payload()
        
        if event_name == "BeforeModel":
            return handle_before_model(payload)
        elif event_name == "BeforeTool":
            return handle_before_tool(payload)
        elif event_name == "AfterTool":
            return handle_after_tool(payload)
        elif event_name == "AfterAgent":
            return handle_after_agent(payload)
        else:
            return emit({"decision": "allow"})
            
    except Exception as e:
        return fail_open(str(e))

if __name__ == "__main__":
    sys.exit(main())
