from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import TypeAlias

from .ledger import JsonObject, state_dir

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class IntentInput:
    goal: str
    scope: Iterable[str]
    non_goals: Iterable[str]
    assumed: bool
    confirmed_at_prompt: str
    ambiguity_score: int


def intent_path(project_root: str) -> Path:
    return state_dir(project_root) / "intent.json"


def has_intent(project_root: str) -> bool:
    return intent_path(project_root).exists()


def load_intent(project_root: str) -> JsonObject:
    path = intent_path(project_root)
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    result: JsonObject = {}
    for key, value in loaded.items():
        if isinstance(key, str) and _json_safe(value):
            result[key] = _json_value(value)
    return result


def save_intent(project_root: str, intent: IntentInput) -> JsonObject:
    record: JsonObject = {
        "goal": intent.goal.strip(),
        "scope": [item.strip() for item in intent.scope if item.strip()],
        "non_goals": [item.strip() for item in intent.non_goals if item.strip()],
        "assumed": intent.assumed,
        "confirmed_at_prompt": intent.confirmed_at_prompt.strip(),
        "ambiguity_score": _bounded_score(intent.ambiguity_score),
    }
    directory = state_dir(project_root)
    directory.mkdir(parents=True, exist_ok=True)
    destination = intent_path(project_root)
    serialized = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=directory,
        prefix="intent-",
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
            pass
    return record


def clear_intent(project_root: str) -> bool:
    try:
        intent_path(project_root).unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _bounded_score(value: int) -> int:
    return max(0, min(4, value))


def _json_safe(value: object) -> bool:
    if isinstance(value, str | int | bool) or value is None:
        return True
    if isinstance(value, list):
        return all(_json_safe(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _json_safe(item) for key, item in value.items())
    return False


def _json_value(value: object) -> JsonValue:
    if isinstance(value, str | int | bool) or value is None:
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value if _json_safe(item)]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items() if isinstance(key, str) and _json_safe(item)}
    return None
