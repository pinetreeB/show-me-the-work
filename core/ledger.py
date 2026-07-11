from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import tempfile
from typing import TypeAlias

from .agent_log import (
    agent_log_path as agent_log_path,
    append_agent_event,
    ledger_transaction,
    load_agent_events,
)

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

DOC_EXTS = (".md", ".txt", ".rst", ".adoc")
CODE_EXTS = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".sql", ".ps1")


def state_dir(project_root: str) -> Path:
    return Path(project_root).resolve() / ".fable-lite"


def ledger_path(project_root: str) -> Path:
    return state_dir(project_root) / "ledger.json"


def default_ledger() -> JsonObject:
    return {
        "task_mode": "quick",
        "prompt": "",
        "packs": [],
        "changed_files_seen": [],
        "change_kinds": [],
        "verification_commands": [],
        "verification_results": [],
        "event_seq": 0,
        "last_change_seq": 0,
        "stop_blocks": 0,
        "goals_blocks": 0,
        "intent_blocks": 0,
        "requires_investigation_compliance": False,
        "needs_goals": False,
        "intent_required": False,
        "ambiguity_score": 0,
        "scope_warnings": [],
        "agent": "",
    }


def _project_root(payload: Mapping[str, object]) -> str:
    root = payload.get("project_root") or payload.get("cwd")
    return root if isinstance(root, str) and root else "."


def _agent(payload: Mapping[str, object]) -> str:
    value = payload.get("agent")
    return value if isinstance(value, str) and value else ""


def load_ledger(payload: Mapping[str, object]) -> JsonObject:
    path = ledger_path(_project_root(payload))
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _preserve_corrupt_ledger(path)
        return default_ledger()
    except OSError:
        return default_ledger()
    if isinstance(loaded, dict):
        merged = default_ledger()
        for key, value in loaded.items():
            if isinstance(key, str):
                merged[key] = value
        return merged
    return default_ledger()


def _preserve_corrupt_ledger(path: Path) -> None:
    if not path.exists():
        return
    backup = path.with_name(f"{path.name}.bak")
    try:
        if backup.exists():
            backup.unlink()
        path.replace(backup)
    except OSError:
        return


def save_ledger(payload: Mapping[str, object], ledger: JsonObject) -> None:
    root = _project_root(payload)
    directory = state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    destination = ledger_path(root)
    serialized = json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=directory,
        prefix="ledger-",
        suffix=".tmp",
    )
    temp_name = handle.name
    try:
        with handle:
            _ = handle.write(serialized)
        os.replace(temp_name, destination)
    except OSError:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            return


def _as_str_list(value: JsonValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_result_list(value: JsonValue | None) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _append_unique(items: list[str], item: str) -> list[str]:
    if item and item not in items:
        items.append(item)
    return items


def classify_change_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in DOC_EXTS:
        return "docs"
    if suffix in CODE_EXTS:
        return "code"
    return "artifact"


def _bool_value(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _bounded_score(value: object) -> int:
    if not isinstance(value, int):
        return 0
    return max(0, min(4, value))


def _sequence_value(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _apply_event(ledger: JsonObject, payload: Mapping[str, object]) -> JsonObject:
    event = payload.get("event")
    event_seq = _sequence_value(payload.get("seq"))
    ledger["event_seq"] = max(
        _sequence_value(ledger.get("event_seq")),
        event_seq,
    )
    agent = _agent(payload)
    if agent:
        ledger["agent"] = agent

    if event == "prompt":
        mode = payload.get("task_mode")
        prompt = payload.get("prompt")
        packs = payload.get("packs")
        needs_goals = payload.get("needs_goals") is True
        intent_required = payload.get("intent_required") is True
        ambiguity_score = _bounded_score(payload.get("ambiguity_score"))
        requires_compliance = payload.get("requires_investigation_compliance") is True
        ledger["task_mode"] = mode if isinstance(mode, str) else "quick"
        ledger["prompt"] = prompt if isinstance(prompt, str) else ""
        ledger["packs"] = (
            [item for item in packs if isinstance(item, str)]
            if isinstance(packs, list)
            else []
        )
        ledger["needs_goals"] = needs_goals
        ledger["intent_required"] = intent_required
        ledger["ambiguity_score"] = ambiguity_score
        ledger["requires_investigation_compliance"] = requires_compliance
        ledger["stop_blocks"] = 0
        ledger["goals_blocks"] = 0
        ledger["intent_blocks"] = 0
        # 변경·검증 기록은 턴 단위 계약이다 — 새 프롬프트에서 리셋하지 않으면
        # 세션 초반에 한 번 수정한 이력이 이후 모든 질문 턴에 changed=True로 남아
        # Stop/N1 게이트가 답변 전용 턴까지 계속 걸린다 (v1.1.3, agy Critical-1).
        ledger["changed_files_seen"] = []
        ledger["change_kinds"] = []
        ledger["verification_commands"] = []
        ledger["verification_results"] = []
        ledger["last_change_seq"] = 0
        ledger["scope_warnings"] = []
    elif event == "change":
        path = payload.get("path")
        if isinstance(path, str) and path:
            changed = _as_str_list(ledger.get("changed_files_seen"))
            kinds = _as_str_list(ledger.get("change_kinds"))
            kind_value = payload.get("kind")
            kind = kind_value if isinstance(kind_value, str) else classify_change_kind(path)
            ledger["changed_files_seen"] = _append_unique(changed, path)
            ledger["change_kinds"] = _append_unique(kinds, kind)
            if kind != "docs":
                ledger["last_change_seq"] = event_seq
    elif event == "verification":
        command_value = payload.get("command")
        evidence_value = payload.get("evidence")
        command = command_value if isinstance(command_value, str) else ""
        evidence = evidence_value if isinstance(evidence_value, str) else ""
        commands = _as_str_list(ledger.get("verification_commands"))
        results = _as_result_list(ledger.get("verification_results"))
        ledger["verification_commands"] = _append_unique(commands, command)
        result: JsonObject = {
            "command": command,
            "success": _bool_value(payload.get("success")),
            "evidence": evidence,
        }
        if event_seq > 0:
            result["seq"] = event_seq
        results.append(result)
        ledger["verification_results"] = results
    elif event == "scope_warning":
        warning_value = payload.get("message")
        warning = warning_value if isinstance(warning_value, str) else ""
        warnings = _as_str_list(ledger.get("scope_warnings"))
        ledger["scope_warnings"] = _append_unique(warnings, warning)

    return ledger


def record_event(payload: Mapping[str, object]) -> JsonObject:
    root = _project_root(payload)
    with ledger_transaction(root):
        ledger = load_ledger(payload)
        event_payload: dict[str, object] = dict(payload)
        _ = event_payload.pop("event_seq", None)
        event_payload["seq"] = _sequence_value(ledger.get("event_seq")) + 1
        _apply_event(ledger, event_payload)
        save_ledger(payload, ledger)
        append_agent_event(root, _agent(payload), event_payload)
        return ledger


def load_agent_ledger(payload: Mapping[str, object]) -> JsonObject:
    agent = _agent(payload)
    if not agent:
        return load_ledger(payload)
    root = _project_root(payload)
    events = load_agent_events(root, agent)
    if events is None:
        return load_ledger(payload)
    ledger = default_ledger()
    for event in events:
        _apply_event(ledger, event)
    return ledger
