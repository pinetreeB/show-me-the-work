from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import assert_never

from adapters.antigravity import hook_common as antigravity
from adapters.antigravity.tool_io import extract_command, extract_paths_from_input, extract_tool_info, verification_result
from adapters.claude_code import common as claude
from adapters.codex_cli import common as codex
from core.adapter_observation import CanonicalInvocation, begin_invocation, finish_turn, observe_post_tool, start_turn
from core.ledger import load_ledger

from .actions import canonical_command, cleanup, execute, prepare
from .models import CorpusCase, Origin, ReplayResult


class Host(StrEnum):
    CLAUDE = "claude_code"
    CODEX = "codex_cli"
    ANTIGRAVITY = "antigravity"


def replay_case(parent: Path, case: CorpusCase) -> ReplayResult:
    semantic: list[tuple[str, str, str, str, str, str, tuple[str, ...], str, bool, str]] = []
    projections: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for host in Host:
        root = parent / host.value
        root.mkdir(parents=True, exist_ok=True)
        layout = prepare(root, case)
        invocation = _invocation(host, case, canonical_command(case))
        try:
            _ = start_turn(root, invocation)
            _ = begin_invocation(root, invocation)
            execute(layout, case)
            _ = observe_post_tool(root, invocation)
            _ = finish_turn(root, invocation)
            semantic.append(_semantic(invocation))
            projections.append(_projection(root))
        finally:
            cleanup(layout)
    return ReplayResult(
        case.case_id,
        len(set(semantic)) == 1,
        len(set(projections)) == 1,
        "payload_injection_not_live_hook",
    )


def _invocation(host: Host, case: CorpusCase, command: str) -> CanonicalInvocation:
    family = _family(case.origin)
    match host:
        case Host.CLAUDE:
            payload = _claude_payload(case, command)
            return claude.canonical_invocation(
                payload,
                "post_tool",
                family,
                claude.tool_file_paths(payload),
                claude.tool_command(payload),
                claude.tool_success(payload),
                claude.tool_output(payload),
            )
        case Host.CODEX:
            payload = _codex_payload(case, command)
            return codex.canonical_invocation(
                payload,
                "post_tool",
                family,
                codex.tool_file_paths(payload),
                codex.tool_command(payload),
                codex.tool_success(payload),
                codex.tool_output(payload),
            )
        case Host.ANTIGRAVITY:
            payload = _antigravity_payload(case, command)
            _, tool_input = extract_tool_info(payload)
            success, evidence = verification_result(payload)
            return antigravity.canonical_invocation(
                payload,
                "post_tool",
                family,
                extract_paths_from_input(tool_input),
                extract_command(tool_input),
                success,
                evidence,
            )
        case unreachable:
            assert_never(unreachable)


def _claude_payload(case: CorpusCase, command: str) -> claude.JsonObject:
    family = _family(case.origin)
    return {
        "agent": "corpus-agent",
        "session_id": "corpus-session",
        "turn_id": "corpus-turn",
        "tool_use_id": case.case_id,
        "tool_input": {"file_path": case.target} if family == "edit" else {"command": command},
        "tool_response": {"success": True, "stdout": "completed"},
    }


def _codex_payload(case: CorpusCase, command: str) -> codex.JsonObject:
    family = _family(case.origin)
    return {
        "agent": "corpus-agent",
        "session_id": "corpus-session",
        "turn_id": "corpus-turn",
        "tool_use_id": case.case_id,
        "tool_input": {"file_path": case.target} if family == "edit" else {"command": command},
        "tool_response": {"success": True, "stdout": "completed"},
    }


def _antigravity_payload(case: CorpusCase, command: str) -> dict[str, object]:
    family = _family(case.origin)
    tool_name = "replace_file_content" if family == "edit" else "shell"
    return {
        "agent": "corpus-agent",
        "session_id": "corpus-session",
        "turn_id": "corpus-turn",
        "tool_use_id": case.case_id,
        "metadata": {
            "tool_name": tool_name,
            "tool_input": {"file_path": case.target} if family == "edit" else {"command": command},
        },
        "tool_response": {"success": True, "llmContent": "completed"},
    }


def _family(origin: Origin) -> str:
    match origin:
        case Origin.EDIT:
            return "edit"
        case Origin.SHELL | Origin.GENERATED | Origin.OVERLAP:
            return "shell"
        case Origin.EXTERNAL:
            return "other"
        case unreachable:
            assert_never(unreachable)


def _semantic(invocation: CanonicalInvocation) -> tuple[str, str, str, str, str, str, tuple[str, ...], str, bool, str]:
    return (
        invocation.agent,
        invocation.session_id,
        invocation.turn_id,
        invocation.invocation_id,
        invocation.phase,
        invocation.tool_family_hint,
        invocation.candidate_paths,
        invocation.command_hint,
        invocation.success,
        invocation.evidence,
    )


def _projection(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    ledger = load_ledger({"project_root": str(root)})
    changed = ledger.get("changed_files_seen")
    kinds = ledger.get("change_kinds")
    paths = tuple(item for item in changed if isinstance(item, str)) if isinstance(changed, list) else ()
    change_kinds = tuple(item for item in kinds if isinstance(item, str)) if isinstance(kinds, list) else ()
    return paths, change_kinds
