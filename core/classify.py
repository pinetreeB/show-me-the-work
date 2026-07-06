from __future__ import annotations

from collections.abc import Mapping
import re
from typing import TypeAlias

from .risk_terms import risk_flags

JsonValue: TypeAlias = str | int | bool | list[str]
JsonObject: TypeAlias = dict[str, JsonValue]

DEBUG_REGEXES = (
    re.compile(r"(버그|고쳐|고치|에러|오류|실패|깨져)"),
    re.compile(r"안\s*(되|돼|됨|됐|될|되지|되는|되네|되나)"),
    re.compile(r"\b(debug|bug|error|fix)\b", re.IGNORECASE),
)
ARTIFACT_PATTERNS = (
    "페이지",
    "화면",
    "렌더",
    "html",
    "svg",
    "게임",
    "차트",
    "만들어",
    "만들어줘",
    "생성",
    "build",
    "render",
    "page",
)
MULTI_STORY_PATTERNS = (
    "그리고",
    "하고",
    "고치고",
    "그리고 또",
    "및",
    "동시에",
    "여러",
    "2개",
    "두 개",
    "multi",
    "and",
)


def _text(payload: Mapping[str, object]) -> str:
    value = payload.get("prompt")
    return value if isinstance(value, str) else ""


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def _is_debug(text: str) -> bool:
    return any(pattern.search(text) for pattern in DEBUG_REGEXES)


def _mentioned_paths(text: str) -> list[str]:
    return re.findall(r"[\w./\\-]+\.[A-Za-z0-9]+", text)


def classify_prompt(payload: Mapping[str, object]) -> JsonObject:
    prompt = _text(payload)
    is_debug = _is_debug(prompt)
    is_artifact = _contains_any(prompt, ARTIFACT_PATTERNS)
    is_multi = _contains_any(prompt, MULTI_STORY_PATTERNS)
    risks = risk_flags(prompt)
    packs: list[str] = []

    if is_debug:
        packs.append("investigation")
    if is_artifact:
        packs.append("verification-grounding")
    if is_multi:
        packs.append("completion")

    mode = "quick"
    if is_debug or risks:
        mode = "deep"
    elif is_artifact or is_multi:
        mode = "normal"
    if is_multi and (is_debug or is_artifact or risks):
        mode = "deep"

    needs_goals = is_multi or len(_mentioned_paths(prompt)) >= 2
    return {
        "mode": mode,
        "packs": packs,
        "risk_flags": risks,
        "needs_goals": needs_goals,
        "requested_paths": _mentioned_paths(prompt),
        "message": "fable-lite: 한국어 라우팅 완료 / routing complete.",
    }
