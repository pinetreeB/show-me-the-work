from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import tempfile
from typing import TypeAlias

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
        "stop_blocks": 0,
        "goals_blocks": 0,
        "requires_investigation_compliance": False,
        "needs_goals": False,
        "scope_warnings": [],
        "agent": "",
    }


def _project_root(payload: Mapping[str, object]) -> str:
    root = payload.get("project_root") or payload.get("cwd")
    return root if isinstance(root, str) and root else "."


def _agent(payload: Mapping[str, object]) -> str:
    value = payload.get("agent")
    return value if isinstance(value, str) and value else ""


def _safe_agent_name(agent: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", agent).strip(".-") or "agent"


def agent_log_path(project_root: str, agent: str) -> Path:
    return state_dir(project_root) / "agents" / f"{_safe_agent_name(agent)}.jsonl"


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


def _json_safe(value: object) -> bool:
    if isinstance(value, str | int | bool) or value is None:
        return True
    if isinstance(value, list):
        return all(_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_safe(item) for key, item in value.items())
    return False


def _json_value(value: object) -> JsonValue | None:
    if isinstance(value, str | int | bool) or value is None:
        return value
    if isinstance(value, list):
        list_values: list[JsonValue] = []
        for item in value:
            if not _json_safe(item):
                return None
            list_values.append(_json_value(item))
        return list_values
    if isinstance(value, dict):
        dict_values: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not _json_safe(item):
                return None
            dict_values[key] = _json_value(item)
        return dict_values
    return None


def _event_payload(payload: Mapping[str, object]) -> JsonObject:
    event: JsonObject = {}
    for key, value in payload.items():
        if not _json_safe(value):
            continue
        event[str(key)] = _json_value(value)
    agent = _agent(payload)
    if agent:
        event["agent"] = agent
    return event


def _append_agent_event(payload: Mapping[str, object]) -> None:
    agent = _agent(payload)
    if not agent:
        return
    root = _project_root(payload)
    path = agent_log_path(root, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = _event_payload(payload)
    event["timestamp"] = datetime.now(UTC).isoformat()
    try:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            _ = handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
            _ = handle.write("\n")
    except OSError:
        return


def _apply_event(ledger: JsonObject, payload: Mapping[str, object]) -> JsonObject:
    event = payload.get("event")
    agent = _agent(payload)
    if agent:
        ledger["agent"] = agent

    if event == "prompt":
        mode = payload.get("task_mode")
        prompt = payload.get("prompt")
        packs = payload.get("packs")
        needs_goals = payload.get("needs_goals") is True
        requires_compliance = payload.get("requires_investigation_compliance") is True
        ledger["task_mode"] = mode if isinstance(mode, str) else "quick"
        ledger["prompt"] = prompt if isinstance(prompt, str) else ""
        ledger["packs"] = (
            [item for item in packs if isinstance(item, str)]
            if isinstance(packs, list)
            else []
        )
        ledger["needs_goals"] = needs_goals
        ledger["requires_investigation_compliance"] = requires_compliance
        ledger["stop_blocks"] = 0
        ledger["goals_blocks"] = 0
    elif event == "change":
        path = payload.get("path")
        if isinstance(path, str) and path:
            changed = _as_str_list(ledger.get("changed_files_seen"))
            kinds = _as_str_list(ledger.get("change_kinds"))
            kind_value = payload.get("kind")
            kind = kind_value if isinstance(kind_value, str) else classify_change_kind(path)
            ledger["changed_files_seen"] = _append_unique(changed, path)
            ledger["change_kinds"] = _append_unique(kinds, kind)
    elif event == "verification":
        command_value = payload.get("command")
        evidence_value = payload.get("evidence")
        command = command_value if isinstance(command_value, str) else ""
        evidence = evidence_value if isinstance(evidence_value, str) else ""
        commands = _as_str_list(ledger.get("verification_commands"))
        results = _as_result_list(ledger.get("verification_results"))
        ledger["verification_commands"] = _append_unique(commands, command)
        results.append(
            {
                "command": command,
                "success": _bool_value(payload.get("success")),
                "evidence": evidence,
            }
        )
        ledger["verification_results"] = results
    elif event == "scope_warning":
        warning_value = payload.get("message")
        warning = warning_value if isinstance(warning_value, str) else ""
        warnings = _as_str_list(ledger.get("scope_warnings"))
        ledger["scope_warnings"] = _append_unique(warnings, warning)

    return ledger


def record_event(payload: Mapping[str, object]) -> JsonObject:
    ledger = load_ledger(payload)
    _apply_event(ledger, payload)
    save_ledger(payload, ledger)
    _append_agent_event(payload)
    return ledger


def load_agent_ledger(payload: Mapping[str, object]) -> JsonObject:
    agent = _agent(payload)
    if not agent:
        return load_ledger(payload)
    root = _project_root(payload)
    path = agent_log_path(root, agent)
    if not path.exists():
        return load_ledger(payload)
    ledger = default_ledger()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return load_ledger(payload)
    for line in lines:
        if not line.strip():
            continue
        try:
            raw: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            _apply_event(ledger, raw)
    return ledger
