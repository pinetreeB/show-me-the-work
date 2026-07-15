from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast


def user_prompt(payload: Mapping[str, object], fallback: str = "") -> str:
    direct = payload.get("prompt")
    if isinstance(direct, str) and direct:
        return direct
    request = _mapping(payload.get("llm_request"))
    messages = request.get("messages")
    if isinstance(messages, Sequence) and not isinstance(messages, str | bytes):
        for raw in reversed(messages):
            message = _mapping(raw)
            if message.get("role") == "user" and (text := _message_text(message)):
                return text
    transcript = payload.get("transcriptPath") or payload.get("transcript_path")
    if isinstance(transcript, str) and transcript:
        if recovered := _transcript_user_prompt(Path(transcript)):
            return recovered
    return fallback


def _transcript_user_prompt(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    last = ""
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = cast(object, json.loads(line))
        except json.JSONDecodeError:
            continue
        record = _mapping(raw)
        message = _mapping(record.get("message"))
        if record.get("role") == "user":
            last = _message_text(record)
        elif message.get("role") == "user":
            last = _message_text(message)
    return last


def _message_text(message: Mapping[str, object]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, Sequence) or isinstance(content, str | bytes):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        mapped = _mapping(item)
        text = mapped.get("text") or mapped.get("content")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}
