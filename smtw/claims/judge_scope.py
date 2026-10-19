"""Explicit operator-provided scope for offline packets and manual judging."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from urllib.parse import unquote
from .paths import normalize_path
from pathlib import Path


@dataclass(frozen=True)
class JudgeScope:
    cases: dict[str, list[str]]
    repositories: list[dict]
    sha256: str
    work_roots: dict[str, list[str]] = field(default_factory=dict)
    checkpoints: dict[str, str] = field(default_factory=dict)


def load_scope(path: Path) -> JudgeScope:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("unsupported judge scope")
    cases = value.get("cases")
    if not isinstance(cases, dict) or not cases:
        raise ValueError("scope needs explicit cases")
    cases = dict(cases)
    work_roots, checkpoints = {}, {}
    for case, entry in list(cases.items()):
        if isinstance(entry, dict):
            prefixes = entry.get('source_prefixes', [])
            roots = entry.get('work_roots', [])
            checkpoint = entry.get('checkpoint')
            if not isinstance(roots, list) or any(not absolute_path(p) for p in roots):
                raise ValueError('work_roots must contain absolute paths')
            if checkpoint is not None and not absolute_path(checkpoint):
                raise ValueError('checkpoint must be one absolute file path')
            work_roots[case] = [canonical(p).rstrip('/') for p in roots]
            if checkpoint is not None:
                checkpoints[case] = canonical(checkpoint)
            cases[case] = prefixes
        else:
            prefixes = entry
        if (not isinstance(case, str) or not case or not isinstance(prefixes, list) or not (prefixes or work_roots.get(case) or checkpoints.get(case))
                or any(not isinstance(p, str) or not p or not p.endswith("/")
                       or ".." in p.replace(chr(92), "/").split("/") for p in prefixes)):
            raise ValueError("invalid scope source prefixes")
    repositories = value.get("repositories", [])
    if not isinstance(repositories, list):
        raise ValueError("invalid evaluation repositories")
    return JudgeScope(cases, repositories, hashlib.sha256(raw).hexdigest(), work_roots, checkpoints)


def canonical(path: str) -> str:
    return normalize_path(unquote(path))


def absolute_path(path) -> bool:
    return isinstance(path, str) and bool(re.match(r"^(?:[a-zA-Z]:[/\\]|/)", path))


def directly_referenced(report: str, path: str) -> bool:
    # Require a complete filename/path token, not a substring of another name.
    name = canonical(path).rsplit("/", 1)[-1]
    text = unquote(report).replace("\\", "/").casefold()
    return bool(name and re.search(r"(?<![\w./-])" + re.escape(name) + r"(?![\w.-])", text)
                or re.search(r"(?<![\w./-])" + re.escape(canonical(path)) + r"(?![\w./-])", text))


def work_source_basis(case, path, scope, *, report=None, visible=False):
    # Display paths are accepted only for already-built packet validation.
    from .packets import display_path
    convert = (lambda p: canonical(display_path(p))) if visible else canonical
    target = convert(path)
    checkpoint = scope.checkpoints.get(case)
    if checkpoint and target == convert(checkpoint):
        return "checkpoint"
    if any(target.startswith(convert(root).rstrip("/") + "/")
           for root in scope.work_roots.get(case, [])):
        if report is None or directly_referenced(report, path):
            return "report_reference"
    return ""
