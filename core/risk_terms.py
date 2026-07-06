from __future__ import annotations

import re
from typing import Final

HIGH_RISK_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b(auth|authorization|permission|payment|billing)\b", re.IGNORECASE),
    re.compile(r"\b(migration|migrations|schema|truncate|drop\s+table|rm\s+-rf)\b", re.IGNORECASE),
    re.compile(r"\b(remove-item)\b.*\b(recurse|force)\b", re.IGNORECASE),
    re.compile(r"\b(mass|bulk|all)\s+delete\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+(all|many|bulk|mass)\b", re.IGNORECASE),
    re.compile(r"(인증|권한|결제|마이그|스키마|대량\s*삭제|전체\s*삭제|일괄\s*삭제)"),
)


def risk_flags(text: str) -> list[str]:
    flags: list[str] = []
    for pattern in HIGH_RISK_PATTERNS:
        match = pattern.search(text)
        if match:
            flags.append(match.group(0))
    return flags


def is_high_risk(text: str) -> bool:
    return bool(risk_flags(text))
