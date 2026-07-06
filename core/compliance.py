from __future__ import annotations

from collections.abc import Mapping
import re
from typing import TypeAlias

JsonValue: TypeAlias = str | int | bool | list[str]
JsonObject: TypeAlias = dict[str, JsonValue]

HYPOTHESIS_RE = re.compile(r"(?:가설|Hypothesis)\s*\d+\s*:", re.IGNORECASE)
REJECTION_RE = re.compile(r"(?:기각|Rejected)\s*:", re.IGNORECASE)
EVIDENCE_RE = re.compile(r"(?:증거|Evidence)\s*:", re.IGNORECASE)


def check_investigation_compliance(payload: Mapping[str, object]) -> JsonObject:
    text_value = payload.get("text")
    text = text_value if isinstance(text_value, str) else ""
    hypothesis_count = len(HYPOTHESIS_RE.findall(text))
    has_rejection = bool(REJECTION_RE.search(text))
    has_evidence = bool(EVIDENCE_RE.search(text))
    missing: list[str] = []

    if hypothesis_count < 3:
        missing.append("hypotheses")
    if not has_rejection:
        missing.append("rejection")
    if not has_evidence:
        missing.append("evidence")

    return {
        "compliant": not missing,
        "hypothesis_count": hypothesis_count,
        "has_rejection": has_rejection,
        "has_evidence": has_evidence,
        "missing": missing,
        "message": (
            "조사 팩 준수 / investigation pack compliant"
            if not missing
            else "조사 팩 마커가 부족합니다 / missing investigation markers"
        ),
    }
