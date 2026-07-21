from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import TypeAlias

from core.agent_log import agent_log_path
from core.contract import (
    evaluate_pretool_contract,
    namespaced_contract_path,
    record_contract_authored_event,
)

ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = ROOT / "adapters" / "codex_cli"
CLAUDE_ADAPTERS = ROOT / "adapters" / "claude_code"

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
HookPayload: TypeAlias = dict[str, JsonValue]
HookOutput: TypeAlias = dict[str, JsonValue]

_ATTRIBUTION_CORRECTION_SNIPPET = (
    'if attribution == "legacy_default" and invocation.session_id != "default":'
)


def run_codex_hook(name: str, payload: HookPayload) -> HookOutput:
    process = subprocess.run(
        [sys.executable, str(ADAPTERS / name)],
        input=json.dumps(payload, ensure_ascii=False),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout or "{}")


def read_agent_log(root: Path, agent: str) -> list[dict[str, JsonValue]]:
    path = agent_log_path(str(root), agent)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _valid_contract_body() -> dict[str, JsonValue]:
    return {
        "restated_goal": "recovered identity probe",
        "acceptance": ["tests pass"],
        "evidence": ["python -m pytest tests/test_x.py"],
    }


def _high_risk_patch(path: str) -> str:
    return f"*** Begin Patch\n*** Add File: {path}\n+DROP TABLE users;\n*** End Patch\n"


# --- Causal unit test: proves *why* the legacy_default/exact distinction matters ---
# for R1, independent of core.adapter_observation._active_invocation's current
# behavior. This is the discriminating "reproduction": it demonstrates the exact
# mechanism CODEX-01/CODEX-02 describe (attribution stuck at legacy_default blocks a
# genuinely valid, already-authored namespaced contract) by constructing both
# payload shapes directly, rather than depending on whichever code path currently
# produces one attribution value or the other.


def test_legacy_default_attribution_blocks_a_valid_recovered_contract_but_exact_allows_it(
    tmp_path: Path,
) -> None:
    agent_key = "codex_cli:codex-session-1:codex"
    namespaced = namespaced_contract_path(str(tmp_path), agent_key)
    namespaced.parent.mkdir(parents=True, exist_ok=True)
    namespaced.write_text(json.dumps(_valid_contract_body()), encoding="utf-8")
    record_contract_authored_event(
        {
            "project_root": str(tmp_path),
            "file_paths": [str(namespaced)],
            "host": "codex_cli",
            "session_id": "codex-session-1",
            "agent": "codex",
            "turn_id": "turn:codex-session-1",
            "attribution": "exact",
        }
    )

    base_payload: dict[str, JsonValue] = {
        "project_root": str(tmp_path),
        "tool_name": "apply_patch",
        "file_paths": [],
        "command": _high_risk_patch("migrations/900.sql"),
        "prompt": "high risk edit",
        "host": "codex_cli",
        "agent": "codex",
        "session_id": "codex-session-1",
        "turn_id": "turn:codex-session-1",
    }

    # Given: the exact same real, already-authored contract and the exact same
    # recovered session identity -- only "attribution" differs, matching the two
    # historical states of a recovered CanonicalInvocation (legacy_default before the
    # correction is applied, exact after).
    stuck_at_legacy_default = evaluate_pretool_contract({**base_payload, "attribution": "legacy_default"})
    promoted_to_exact = evaluate_pretool_contract({**base_payload, "attribution": "exact"})

    # Then: an un-promoted recovered identity is wrongly denied its own valid
    # contract (the CODEX-01/CODEX-02 failure mode); promotion fixes it.
    assert stuck_at_legacy_default["decision"] == "block"
    assert promoted_to_exact["decision"] == "allow"


# --- Structural tests: codex_cli's hooks must carry the same promotion snippet ---
# claude_code's adapters already carry (CODEX-01/CODEX-02, and the "3-adapter order
# invariant" requested alongside them). These fail before the fix and pass after.


def test_claude_code_pretool_has_the_reference_promotion_snippet() -> None:
    # Sanity check on the reference implementation itself, so this test file does not
    # silently pass if claude_code's own pattern is ever renamed/removed.
    source = (CLAUDE_ADAPTERS / "pre_tool_use.py").read_text(encoding="utf-8")
    assert _ATTRIBUTION_CORRECTION_SNIPPET in source


def test_claude_code_posttool_has_the_reference_promotion_snippet() -> None:
    source = (CLAUDE_ADAPTERS / "post_tool_use.py").read_text(encoding="utf-8")
    assert _ATTRIBUTION_CORRECTION_SNIPPET in source


def test_codex_pretool_promotes_recovered_legacy_default_to_exact_before_contract_check() -> None:
    # CODEX-01: adapters/codex_cli/pre_tool_use.py must promote a resolved
    # (non-"default" session_id) legacy_default attribution to exact before calling
    # evaluate_pretool_contract, matching adapters/claude_code/pre_tool_use.py.
    source = (ADAPTERS / "pre_tool_use.py").read_text(encoding="utf-8")
    assert _ATTRIBUTION_CORRECTION_SNIPPET in source
    resolve_at = source.index("resolve_active_invocation(")
    correction_at = source.index(_ATTRIBUTION_CORRECTION_SNIPPET)
    contract_call_at = source.index("evaluate_pretool_contract(")
    assert resolve_at < correction_at < contract_call_at, (
        "promotion must run after identity resolution and before the contract check"
    )


def test_codex_posttool_promotes_recovered_legacy_default_to_exact_before_recording_authorship() -> None:
    # CODEX-02: adapters/codex_cli/post_tool_use.py must resolve identity, then
    # promote attribution, then record_contract_authored_event -- in that order.
    # (Note: on the current codebase resolve_active_invocation already runs before
    # record_contract_authored_event; the gap this closes is the missing promotion
    # step in between, not a reversed call order.)
    source = (ADAPTERS / "post_tool_use.py").read_text(encoding="utf-8")
    assert _ATTRIBUTION_CORRECTION_SNIPPET in source
    resolve_at = source.index("resolve_active_invocation(")
    correction_at = source.index(_ATTRIBUTION_CORRECTION_SNIPPET)
    record_at = source.index("record_contract_authored_event(")
    assert resolve_at < correction_at < record_at, (
        "promotion must run after identity resolution and before recording authorship"
    )


# --- End-to-end (real hook subprocess) regression tests -----------------------
# These currently pass even before the adapter-file fix, because an unrelated F2
# turn-bootstrap-atomicity rewrite (591eedd) incidentally started clearing
# identity_synthetic on recovery too. They are added here as explicit regression
# coverage (none existed before) so that guarantee cannot silently regress again --
# and, after the fix, they are also backed by the adapters' own defensive promotion
# rather than only by core.adapter_observation._active_invocation's internals.


def test_codex_recovered_identity_end_to_end_promotes_authors_contract_and_passes_high_risk_edit(
    tmp_path: Path,
) -> None:
    agent_key = "codex_cli:codex-session-1:codex"
    namespaced = namespaced_contract_path(str(tmp_path), agent_key)

    # Turn 1: a real session_id present from the start bootstraps the turn.
    bootstrap = run_codex_hook(
        "pre_tool_use.py",
        {"cwd": str(tmp_path), "session_id": "codex-session-1", "tool_name": "Read", "tool_input": {"path": "app.py"}},
    )
    assert bootstrap.get("decision") != "block"

    # Turn 1: author its own namespaced contract.
    namespaced.parent.mkdir(parents=True, exist_ok=True)
    namespaced.write_text(json.dumps(_valid_contract_body()), encoding="utf-8")
    pre_author = run_codex_hook(
        "pre_tool_use.py",
        {
            "cwd": str(tmp_path),
            "session_id": "codex-session-1",
            "tool_name": "Write",
            "tool_input": {"file_paths": [str(namespaced)]},
        },
    )
    assert pre_author.get("decision") != "block"
    run_codex_hook(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "session_id": "codex-session-1",
            "tool_name": "Write",
            "tool_input": {"file_paths": [str(namespaced)]},
            "tool_response": "Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess.\n",
        },
    )

    # contract_authored event + content digest must be recorded (audit cross-check).
    events = [event for event in read_agent_log(tmp_path, "codex") if event.get("event") == "contract_authored"]
    assert len(events) == 1
    assert events[0]["contract_path"] == namespaced.relative_to(tmp_path).as_posix()
    assert isinstance(events[0].get("content_digest"), str) and events[0]["content_digest"]

    # Turn 2 (same logical session, but this call omits session_id -- the recovered
    # identity scenario): a high-risk edit must pass using the contract authored
    # under the real session, not be blocked as an unrelated/unauthenticated identity.
    recovered = run_codex_hook(
        "pre_tool_use.py",
        {"cwd": str(tmp_path), "tool_name": "apply_patch", "tool_input": {"command": _high_risk_patch("migrations/901.sql")}},
    )
    assert recovered.get("decision") != "block", recovered


def test_codex_a_different_identitys_contract_is_still_rejected_for_a_recovered_call(
    tmp_path: Path,
) -> None:
    # A recovered identity must not be able to ride on a namespaced contract that
    # belongs to some other, unrelated session/agent.
    other_key = "codex_cli:someone-elses-session:codex"
    other_namespaced = namespaced_contract_path(str(tmp_path), other_key)
    other_namespaced.parent.mkdir(parents=True, exist_ok=True)
    other_namespaced.write_text(json.dumps(_valid_contract_body()), encoding="utf-8")
    record_contract_authored_event(
        {
            "project_root": str(tmp_path),
            "file_paths": [str(other_namespaced)],
            "host": "codex_cli",
            "session_id": "someone-elses-session",
            "agent": "codex",
            "turn_id": "turn:someone-elses-session",
            "attribution": "exact",
        }
    )
    # Bootstrap an unrelated real session so there is exactly one *other* active
    # turn to potentially (and wrongly) recover into.
    run_codex_hook(
        "pre_tool_use.py",
        {"cwd": str(tmp_path), "session_id": "someone-elses-session", "tool_name": "Read", "tool_input": {"path": "app.py"}},
    )

    result = run_codex_hook(
        "pre_tool_use.py",
        {
            "cwd": str(tmp_path),
            "session_id": "my-own-session",
            "tool_name": "apply_patch",
            "tool_input": {"command": _high_risk_patch("migrations/902.sql")},
        },
    )

    assert result.get("decision") == "block"
