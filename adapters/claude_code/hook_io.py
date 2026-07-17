from __future__ import annotations

from collections.abc import Mapping
import json
import sys
from typing import TypeAlias


JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class PayloadError(Exception):
    pass


def read_payload() -> JsonObject:
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PayloadError(f"malformed JSON payload: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise PayloadError("hook payload must be a JSON object")
    return {
        str(key): value
        for key, value in raw.items()
        if isinstance(key, str) and json_value(value)
    }


def emit(payload: Mapping[str, JsonValue]) -> int:
    data = json.dumps(dict(payload), ensure_ascii=False)
    _ = sys.stdout.buffer.write(data.encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def fail_open(message: str) -> int:
    return emit({"systemMessage": f"[smtw] health: fail-open: {message}"})


def string_value(value: JsonValue | object) -> str:
    return value if isinstance(value, str) else ""


def json_value(value: object) -> bool:
    if isinstance(value, str | int | bool) or value is None:
        return True
    if isinstance(value, list):
        return all(json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and json_value(item) for key, item in value.items()
        )
    return False
