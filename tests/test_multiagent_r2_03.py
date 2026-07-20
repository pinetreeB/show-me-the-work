from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.provenance_policy as provenance_policy
from core.adapter_observation import CanonicalInvocation, begin_invocation
from core.destructive_guard import evaluate_r2_destructive_gate
from core.ledger import JsonObject, load_ledger


PEER_AGENT_KEY = "codex_cli:peer-session:peer"


def _record_peer_candidate(root: Path, candidate: str, invocation_id: str) -> list[str]:
    report = begin_invocation(
        root,
        CanonicalInvocation(
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
        ),
    )
    assert report.incomplete is False
    ledger = load_ledger({"project_root": str(root)})
    active = ledger.get("active_turns")
    assert isinstance(active, dict)
    turn = active.get(PEER_AGENT_KEY)
    assert isinstance(turn, dict)
    invocations = turn.get("invocations")
    assert isinstance(invocations, dict)
    invocation = invocations.get(invocation_id)
    assert isinstance(invocation, dict)
    candidates = invocation.get("candidate_paths")
    assert isinstance(candidates, list)
    return [item for item in candidates if isinstance(item, str)]


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


def test_peer_absolute_candidate_blocks_relative_destructive_target(
    tmp_path: Path,
) -> None:
    absolute = tmp_path / "peer-new.py"

    stored = _record_peer_candidate(tmp_path, str(absolute), "absolute-peer")
    result = _r2_result(tmp_path, "peer-new.py")

    assert result["decision"] == "block"
    assert "peer_open_invocation_candidate" in str(result["reason"])
    assert stored == ["peer-new.py"]


def test_peer_relative_candidate_blocks_absolute_destructive_target(
    tmp_path: Path,
) -> None:
    absolute = tmp_path / "peer-new.py"

    stored = _record_peer_candidate(tmp_path, "peer-new.py", "relative-peer")
    result = _r2_result(tmp_path, str(absolute))

    assert stored == ["peer-new.py"]
    assert result["decision"] == "block"


def test_windows_casefold_candidate_matches_target(tmp_path: Path) -> None:
    canonicalize = getattr(provenance_policy, "canonicalize_project_path", None)

    assert canonicalize is not None
    candidate = canonicalize(tmp_path, str(tmp_path / "Peer-New.PY"), windows=True)
    target = canonicalize(tmp_path, "peer-new.py", windows=True)
    assert candidate == ("in_root", "peer-new.py")
    assert target == candidate


def test_out_of_root_candidate_is_not_misclassified_as_project_owned(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-peer.py"

    stored = _record_peer_candidate(tmp_path, str(outside), "outside-peer")
    result = _r2_result(tmp_path, "outside-peer.py")

    assert stored == []
    assert result["decision"] == "allow"


def _overwrite_stored_candidate_paths(
    root: Path, invocation_id: str, raw_paths: list[str]
) -> None:
    # Simulates a pre-upgrade ledger entry: candidate_paths as they would have been
    # stored *before* _canonical_candidate_paths existed (commit 9610115), i.e. raw
    # / possibly-absolute strings written verbatim, never passed through
    # canonicalize_project_path at write time. New writes can no longer produce this
    # shape; this directly edits the on-disk ledger to reproduce what an already-open
    # peer invocation from an older version would look like at read time.
    ledger_path = root / ".fable-lite" / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    invocation = ledger["active_turns"][PEER_AGENT_KEY]["invocations"][invocation_id]
    invocation["candidate_paths"] = raw_paths
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")


def test_legacy_absolute_candidate_stored_before_write_side_fix_still_blocks_relative_target(
    tmp_path: Path,
) -> None:
    # R2-03 backward-compat gap (agy Critical finding): a peer invocation that was
    # already open with a raw absolute candidate_paths entry -- e.g. because it was
    # recorded before the write-side normalization in _canonical_candidate_paths
    # existed, or by any future writer that does not go through it -- must still be
    # read back correctly by open_peer_invocation_candidates. The read side must not
    # rely solely on the write side always having normalized first.
    absolute = tmp_path / "peer-new.py"
    invocation_id = "legacy-absolute-peer"

    _record_peer_candidate(tmp_path, "peer-new.py", invocation_id)
    _overwrite_stored_candidate_paths(tmp_path, invocation_id, [str(absolute)])

    result = _r2_result(tmp_path, "peer-new.py")

    assert result["decision"] == "block"
    assert "peer_open_invocation_candidate" in str(result["reason"])


def test_unresolvable_candidate_is_not_used_as_mitigation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonicalize = getattr(provenance_policy, "canonicalize_project_path", None)
    assert canonicalize is not None
    real_resolve = Path.resolve

    def fail_candidate_resolve(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> Path:
        if path.name == "unresolvable-peer.py":
            raise OSError("simulated candidate resolution failure")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_candidate_resolve)

    assert canonicalize(tmp_path, "unresolvable-peer.py") == ("unresolvable", None)
