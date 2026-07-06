from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

from core.classify import classify_prompt
from core.contract import evaluate_r1_contract
from core.ledger import JsonObject, classify_change_kind, load_agent_ledger, load_ledger
from core.scope_guard import evaluate_scope

SENTINEL_RE = re.compile(r"(?P<path>(?:[\w./\\-]+/)?\.done[\w_.-]*|tmp[/\\]\.done[\w_.-]*)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CheckResult:
    root: Path
    agent: str
    changed_files: list[str]
    unverified: list[str]
    scope_messages: list[str]
    r1_messages: list[str]
    promise_messages: list[str]
    git_warnings: list[str]

    def is_green(self) -> bool:
        return not (self.unverified or self.scope_messages or self.r1_messages or self.promise_messages)


def run_check(args: Namespace) -> int:
    root = Path(str(args.root)).resolve()
    agent = str(args.agent or "")
    since_file = Path(str(args.since_file)).resolve() if args.since_file else None
    result = evaluate(root, agent, since_file)
    print(render(result))
    return 0 if result.is_green() else 1


def evaluate(root: Path, agent: str, since_file: Path | None) -> CheckResult:
    ledger_payload = {"project_root": str(root), "agent": agent} if agent else {"project_root": str(root)}
    ledger = load_agent_ledger(ledger_payload) if agent else load_ledger(ledger_payload)
    changed_files, warnings = changed_paths(root, ledger, since_file)
    prompt = _string(ledger.get("prompt"))
    unverified = unverified_changes(changed_files, ledger)
    scope_messages = scope_findings(root, prompt, changed_files)
    r1_messages = r1_findings(root, prompt, changed_files)
    promise_messages = sentinel_findings(root, prompt)
    return CheckResult(
        root=root,
        agent=agent,
        changed_files=changed_files,
        unverified=unverified,
        scope_messages=scope_messages,
        r1_messages=r1_messages,
        promise_messages=promise_messages,
        git_warnings=warnings,
    )


def render(result: CheckResult) -> str:
    status = "GREEN" if result.is_green() else "RED"
    lines = [
        f"fable-lite check: {status}",
        f"- root: {result.root}",
        f"- agent: {result.agent or '(all)'}",
        f"- changed: {len(result.changed_files)}",
    ]
    if result.changed_files:
        lines.extend(f"  - {path}" for path in result.changed_files)
    _section(lines, "미검증 변경", result.unverified)
    _section(lines, "scope 이탈", result.scope_messages)
    _section(lines, "R1 위반", result.r1_messages)
    _section(lines, "미이행 약속", result.promise_messages)
    _section(lines, "git 경고", result.git_warnings)
    return "\n".join(lines)


def changed_paths(root: Path, ledger: Mapping[str, object], since_file: Path | None) -> tuple[list[str], list[str]]:
    paths = _string_list(ledger.get("changed_files_seen"))
    warnings: list[str] = []
    git_result = _git(root, "status", "--porcelain=v1")
    if git_result.returncode == 0:
        paths = _merge(paths, _parse_porcelain(git_result.stdout))
    else:
        warnings.append("git status 실행 실패: ledger 기준으로만 판정")
    paths = [path for path in paths if not _is_state_path(path)]
    if since_file is not None:
        marker = _relative_to_root(root, since_file)
        paths = [path for path in paths if path != marker]
        paths = [path for path in paths if _changed_since(root, path, since_file)]
    return paths, warnings


def unverified_changes(changed_files: list[str], ledger: Mapping[str, object]) -> list[str]:
    if not changed_files or _has_successful_verification(ledger):
        return []
    return [path for path in changed_files if classify_change_kind(path) != "docs"]


def scope_findings(root: Path, prompt: str, changed_files: list[str]) -> list[str]:
    if not changed_files:
        return []
    classified = classify_prompt({"prompt": prompt})
    requested_paths = classified.get("requested_paths")
    result = evaluate_scope(
        {
            "project_root": str(root),
            "prompt": prompt,
            "requested_paths": requested_paths if isinstance(requested_paths, list) else [],
            "changed_files": changed_files,
        }
    )
    if result.get("decision") != "warn":
        return []
    out_of_scope = _string_list(result.get("out_of_scope"))
    message = _string(result.get("message")) or "범위 이탈 가능성"
    return [f"{path}: {message}" for path in out_of_scope]


def r1_findings(root: Path, prompt: str, changed_files: list[str]) -> list[str]:
    findings: list[str] = []
    for path in changed_files:
        risk_text = "\n".join([prompt, path, _path_evidence(root, path)])
        result = evaluate_r1_contract(
            {
                "project_root": str(root),
                "tool_name": "Edit",
                "file_paths": [path],
                "prompt": risk_text,
            }
        )
        if result.get("decision") == "block":
            reason = _string(result.get("reason")) or "R1 contract required"
            findings.append(f"{path}: {reason}")
    return findings


def sentinel_findings(root: Path, prompt: str) -> list[str]:
    missing: list[str] = []
    for sentinel in _sentinels(prompt):
        if not (root / sentinel).exists():
            missing.append(f"{sentinel}: sentinel 파일이 없습니다")
    return missing


def _section(lines: list[str], title: str, items: list[str]) -> None:
    if not items:
        return
    lines.append(f"- {title}:")
    lines.extend(f"  - {item}" for item in items)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _parse_porcelain(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        value = line[3:]
        if " -> " in value:
            value = value.rsplit(" -> ", 1)[1]
        paths.append(value.replace("\\", "/"))
    return paths


def _is_state_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized == ".fable-lite" or normalized.startswith(".fable-lite/")


def _changed_since(root: Path, path: str, since_file: Path) -> bool:
    if not since_file.exists():
        return True
    target = root / path
    try:
        return not target.exists() or target.stat().st_mtime >= since_file.stat().st_mtime
    except OSError:
        return True


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""


def _path_evidence(root: Path, path: str) -> str:
    target = root / path
    if target.exists() and target.is_file():
        try:
            return target.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            return ""
    diff = _git(root, "diff", "--", path)
    return diff.stdout[:4000] if diff.returncode == 0 else ""


def _sentinels(prompt: str) -> list[str]:
    found: list[str] = []
    for match in SENTINEL_RE.finditer(prompt):
        path = match.group("path").replace("\\", "/")
        if path not in found:
            found.append(path)
    return found


def _has_successful_verification(ledger: Mapping[str, object]) -> bool:
    results = ledger.get("verification_results")
    if not isinstance(results, list):
        return False
    return any(isinstance(result, dict) and result.get("success") is True for result in results)


def _merge(first: list[str], second: list[str]) -> list[str]:
    merged = list(first)
    for item in second:
        if item and item not in merged:
            merged.append(item)
    return merged


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
