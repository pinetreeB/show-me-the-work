from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest

from core.adapter_observation import (
    CanonicalInvocation,
    begin_invocation,
    observe_post_tool,
)
from core.destructive_guard import evaluate_r2_destructive_gate
from core.ledger import JsonObject, load_agent_events, load_ledger
import core.provenance_policy as provenance_policy


PEER_AGENT_KEY = "codex_cli:peer-session:peer"


def _invocation(candidate: str, invocation_id: str) -> CanonicalInvocation:
    return CanonicalInvocation(
        host="codex_cli",
        agent="peer",
        session_id="peer-session",
        turn_id="peer-turn",
        invocation_id=invocation_id,
        phase="pre_tool",
        tool_family_hint="edit",
        candidate_paths=(candidate,),
        command_hint="",
        success=True,
        evidence="",
    )


def _begin(root: Path, candidate: str, invocation_id: str) -> tuple[CanonicalInvocation, JsonObject]:
    invocation = _invocation(candidate, invocation_id)
    report = begin_invocation(root, invocation)
    assert report.incomplete is False
    ledger = load_ledger({"project_root": str(root)})
    active = cast(dict[str, object], ledger["active_turns"])
    turn = cast(JsonObject, active[PEER_AGENT_KEY])
    invocations = cast(dict[str, object], turn["invocations"])
    return invocation, cast(JsonObject, invocations[invocation_id])


def _r2_result(root: Path, target: str) -> JsonObject:
    return evaluate_r2_destructive_gate(
        {
            "project_root": str(root),
            "tool_name": "Bash",
            "command": f'rm "{target}"',
            "host": "claude_code",
            "agent": "caller",
            "session_id": "caller-session",
        },
        lookup_path_attribution=lambda _ledger, _canonical: None,
        attribution_health=lambda _ledger: {
            "degraded": False,
            "capacity_exceeded": False,
        },
    )


def _symlink(link: Path, target: str | Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {exc}")


def test_symlink_replace_uses_logical_candidate_for_post_tool_attribution(
    tmp_path: Path,
) -> None:
    target = tmp_path / "real.txt"
    target.write_text("target", encoding="utf-8")
    link = tmp_path / "link.txt"
    _symlink(link, target.name)
    invocation, entry = _begin(tmp_path, "link.txt", "symlink-replace")

    replacement = tmp_path / "replacement.txt"
    replacement.write_text("replacement", encoding="utf-8")
    os.replace(replacement, link)
    report = observe_post_tool(tmp_path, invocation)

    assert report.incomplete is False
    assert entry["candidate_logical_paths"] == ["link.txt"]
    assert entry["candidate_resolved_paths"] == ["real.txt"]
    events = load_agent_events(str(tmp_path), "peer")
    assert events is not None
    changes = [event for event in events if event.get("event") == "change"]
    paths = cast(list[JsonObject], changes[-1]["paths"])
    assert paths[0]["path"] == "link.txt"
    assert changes[-1]["source"] == "edit"


def test_symlink_target_edit_registers_resolved_r2_candidate(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("before", encoding="utf-8")
    link = tmp_path / "link.txt"
    _symlink(link, target.name)

    _invocation_value, entry = _begin(tmp_path, "link.txt", "target-edit")
    result = _r2_result(tmp_path, "real.txt")

    assert entry["candidate_logical_paths"] == ["link.txt"]
    assert entry["candidate_resolved_paths"] == ["real.txt"]
    assert result["decision"] == "block"
    assert "peer_open_invocation_candidate" in str(result["reason"])


def test_relative_and_absolute_candidates_share_project_relative_dual_keys(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "src" / "app.py"
    candidate.parent.mkdir()
    candidate.write_text("pass\n", encoding="utf-8")

    _relative, relative_entry = _begin(tmp_path, "src/app.py", "relative")
    _absolute, absolute_entry = _begin(tmp_path, str(candidate), "absolute")

    for entry in (relative_entry, absolute_entry):
        assert entry["candidate_logical_paths"] == ["src/app.py"]
        assert entry["candidate_resolved_paths"] == ["src/app.py"]


def test_logical_and_resolved_candidates_share_windows_case_policy(
    tmp_path: Path,
) -> None:
    logical = provenance_policy.canonicalize_project_logical_path(
        tmp_path,
        str(tmp_path / "Src" / "Peer.PY"),
        windows=True,
    )
    resolved = provenance_policy.canonicalize_project_path(
        tmp_path,
        str(tmp_path / "Src" / "Peer.PY"),
        windows=True,
    )

    assert logical == ("in_root", "src/peer.py")
    assert resolved == logical


def test_broken_symlink_keeps_logical_and_current_resolved_keys(tmp_path: Path) -> None:
    link = tmp_path / "broken.txt"
    _symlink(link, "missing.txt")

    _invocation_value, entry = _begin(tmp_path, "broken.txt", "broken")

    assert entry["candidate_logical_paths"] == ["broken.txt"]
    assert entry["candidate_resolved_paths"] == ["missing.txt"]
    assert _r2_result(tmp_path, "missing.txt")["decision"] == "block"


def test_out_of_root_symlink_is_logical_only_and_follows_r2_outside_policy(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "outside-link.txt"
    _symlink(link, outside)

    _invocation_value, entry = _begin(tmp_path, "outside-link.txt", "outside")

    assert entry["candidate_logical_paths"] == ["outside-link.txt"]
    assert entry["candidate_resolved_paths"] == []
    assert _r2_result(tmp_path, "outside-link.txt")["decision"] == "allow"
