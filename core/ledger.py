from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import tempfile
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

DOC_EXTS = {".md", ".txt", ".rst", ".adoc"}
CODE_EXTS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".sql",
    ".ps1",
}


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
    }


def _project_root(payload: Mapping[str, object]) -> str:
    root = payload.get("project_root") or payload.get("cwd")
    return root if isinstance(root, str) and root else "."


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


def record_event(payload: Mapping[str, object]) -> JsonObject:
    ledger = load_ledger(payload)
    event = payload.get("event")

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

    save_ledger(payload, ledger)
    return ledger
