"""Explicit operator-provided scope for offline packets and manual judging."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path


@dataclass(frozen=True)
class JudgeScope:
    cases: dict[str, list[str]]
    repositories: list[dict]
    sha256: str


def load_scope(path: Path) -> JudgeScope:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("unsupported judge scope")
    cases = value.get("cases")
    if not isinstance(cases, dict) or not cases:
        raise ValueError("scope needs explicit cases")
    for case, prefixes in cases.items():
        if (not isinstance(case, str) or not case or not isinstance(prefixes, list) or not prefixes
                or any(not isinstance(p, str) or not p or not p.endswith("/")
                       or ".." in p.replace(chr(92), "/").split("/") for p in prefixes)):
            raise ValueError("invalid scope source prefixes")
    repositories = value.get("repositories", [])
    if not isinstance(repositories, list):
        raise ValueError("invalid evaluation repositories")
    return JudgeScope(cases, repositories, hashlib.sha256(raw).hexdigest())
