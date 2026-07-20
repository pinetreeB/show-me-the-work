from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import core.adapter_observation as adapter_observation
import core.provenance_lifecycle as lifecycle_module
from core.adapter_observation import CanonicalInvocation, begin_invocation
from core.agent_log import load_agent_events
from core.ledger import JsonValue, load_ledger, record_event
from core.provenance_lifecycle import ProvenanceLifecycle, adjust_snapshot_for_peer_activity
from core.provenance_lifecycle_types import Invocation, ObservationResult, TurnState
from core.provenance_turn_resume import TurnBootstrapError
from core.scorecard_coordination import (
    CoordinationOutcome,
    CoordinationReason,
    load_coordination_journal,
)
from core.provenance_types import (
    EntryKind,
    ManifestEntry,
    ProvenanceReason,
    ProvenanceStatus,
    ScanIssue,
    Snapshot,
    SnapshotExclusion,
)
from core.verification_covers import active_turn
from core.verify_state import evaluate_without_io


def _snapshot(
    root: Path,
    *,
    entries: tuple[ManifestEntry, ...],
    issues: tuple[ScanIssue, ...] = (),
) -> Snapshot:
    return Snapshot(
        root=root,
        entries=entries,
        reparse_observations=(),
        issues=issues,
        snapshot_id="snapshot:input",
        scope_policy_id="policy",
        generated_patterns=(),
        status=ProvenanceStatus.INCOMPLETE if issues else ProvenanceStatus.COMPLETE,
    )


def _entry(path: str, digest: str) -> ManifestEntry:
    return ManifestEntry(
        path=path,
        canonical_key=path,
        file_type=EntryKind.REGULAR,
        size=7,
        mtime_ns=1,
        mode=0o644,
        digest=digest,
    )


def _record_peer_window(
    root: Path,
    path: str,
    *,
    timestamp: str | None = None,
) -> None:
    base: dict[str, JsonValue] = {
        "project_root": str(root),
        "host": "host",
        "session_id": "peer-session",
        "agent": "peer",
        "turn_id": "peer-turn",
        "attribution": "exact",
    }
    _ = record_event(
        base
        | {
            "event": "prompt",
            "prompt": "peer work",
            "baseline_snapshot_id": "snapshot:base",
            "current_snapshot_id": "snapshot:base",
        }
    )
    invocation: dict[str, JsonValue] = {
        "event": "invocation",
        "invocation_id": "peer-open",
        "candidate_paths": [path],
    }
    if timestamp is not None:
        invocation["timestamp"] = timestamp
    _ = record_event(base | invocation)


def _start_caller_before_shared_update(root: Path) -> ProvenanceLifecycle:
    (root / "own.py").write_text("before", encoding="utf-8")
    (root / "peer.py").write_text("before", encoding="utf-8")
    caller = ProvenanceLifecycle(root)
    _ = caller.start_turn("caller", "caller-turn")
    updater = ProvenanceLifecycle(root)
    _ = updater.start_turn("updater", "updater-turn")
    invocation = updater.begin_invocation(
        "updater",
        "updater-turn",
        "updater-post",
        ("own.py", "peer.py"),
    )
    (root / "own.py").write_text("after", encoding="utf-8")
    (root / "peer.py").write_text("after", encoding="utf-8")
    updated = updater.post_tool(invocation, "edit")
    assert {change.path for change in updated.changes} == {"own.py", "peer.py"}
    return caller


def _install_peer_exclusion_scan(
    root: Path,
    lifecycle: ProvenanceLifecycle,
    monkeypatch: pytest.MonkeyPatch,
    *,
    scan_physical: bool,
) -> None:
    def exclude_peer(
        previous: Snapshot | None,
        forced_paths: frozenset[str],
        full_scan: bool,
    ) -> Snapshot:
        assert previous is not None
        observed = (
            lifecycle_module.scan_snapshot(
                root,
                previous,
                forced_paths,
                full_scan,
            )
            if scan_physical
            else previous
        )
        return replace(
            observed,
            entries=tuple(
                entry for entry in observed.entries if entry.path != "peer.py"
            ),
            issues=(ScanIssue("peer.py", "unstable_path"),),
            exclusions=(),
            snapshot_id="snapshot:peer-unstable",
            status=ProvenanceStatus.INCOMPLETE,
            status_reason=ProvenanceReason.OBSERVATION_ERROR,
        )

    monkeypatch.setattr(lifecycle, "_scan", exclude_peer)


def test_recorded_peer_hot_write_becomes_complete_with_exclusions(tmp_path: Path) -> None:
    # Given: a prior entry becomes unstable while a recorded peer invocation owns that candidate.
    previous_entry = _entry("hot.py", "digest:before")
    previous = _snapshot(tmp_path, entries=(previous_entry,))
    unstable = _snapshot(
        tmp_path,
        entries=(),
        issues=(ScanIssue("hot.py", "unstable_path"),),
    )
    _record_peer_window(tmp_path, "hot.py")

    # When: provenance reconciles the failed capture against active peer evidence.
    adjusted = adjust_snapshot_for_peer_activity(
        tmp_path,
        unstable,
        previous,
        "host:caller-session:caller",
        "caller-turn",
        now=datetime.now(UTC),
    )

    # Then: the previous entry and auditable exclusion evidence replace a false delete delta.
    assert adjusted.status is ProvenanceStatus.COMPLETE_WITH_EXCLUSIONS
    assert adjusted.status_reason is ProvenanceReason.PEER_ACTIVITY
    assert adjusted.entries == (previous_entry,)
    assert adjusted.issues == ()
    assert len(adjusted.exclusions) == 1
    assert adjusted.exclusions[0].path == "hot.py"
    assert adjusted.exclusions[0].invocation_id == "peer-open"
    assert adjusted.snapshot_id != unstable.snapshot_id


def test_unrecorded_hot_write_remains_observation_error(tmp_path: Path) -> None:
    # Given: an unstable path has no matching recorded peer invocation.
    previous = _snapshot(tmp_path, entries=(_entry("hot.py", "digest:before"),))
    unstable = _snapshot(
        tmp_path,
        entries=(),
        issues=(ScanIssue("hot.py", "unreadable_path"),),
    )

    # When: peer adjustment is attempted without ledger evidence.
    adjusted = adjust_snapshot_for_peer_activity(
        tmp_path,
        unstable,
        previous,
        "host:caller-session:caller",
        "caller-turn",
    )

    # Then: the original incomplete snapshot remains conservative.
    assert adjusted.status is ProvenanceStatus.INCOMPLETE
    assert adjusted.issues == unstable.issues
    assert adjusted.exclusions == ()


def test_excluded_path_is_forced_on_the_next_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a workspace snapshot carries one turn-scoped peer exclusion.
    previous = _snapshot(tmp_path, entries=(_entry("hot.py", "digest:before"),))
    unstable = _snapshot(
        tmp_path,
        entries=(),
        issues=(ScanIssue("hot.py", "unstable_path"),),
    )
    _record_peer_window(tmp_path, "hot.py")
    adjusted = adjust_snapshot_for_peer_activity(
        tmp_path,
        unstable,
        previous,
        "host:caller-session:caller",
        "caller-turn",
    )
    seen: list[frozenset[str]] = []

    def fake_scan(root: Path, prior: Snapshot | None, forced: frozenset[str], full: bool) -> Snapshot:
        seen.append(forced)
        return previous

    # When: the next lifecycle scan starts with no explicit candidate paths.
    monkeypatch.setattr(lifecycle_module, "scan_snapshot", fake_scan)
    result = ProvenanceLifecycle(tmp_path)._scan(adjusted, frozenset(), False)

    # Then: the excluded path is reobserved instead of being reused indefinitely.
    assert result is previous
    assert seen == [frozenset({"hot.py"})]


def test_post_tool_replays_only_non_excluded_turn_deltas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: shared current advanced both paths after the caller's turn baseline.
    caller = _start_caller_before_shared_update(tmp_path)
    _record_peer_window(tmp_path, "peer.py")
    invocation = caller.begin_invocation(
        "caller",
        "caller-turn",
        "caller-post",
        ("own.py", "peer.py"),
        prime_candidates=False,
    )
    _install_peer_exclusion_scan(
        tmp_path,
        caller,
        monkeypatch,
        scan_physical=False,
    )

    # When: this post excludes the peer path while replaying the caller baseline.
    result = caller.post_tool(invocation, "edit")

    # Then: only the trustworthy path is attributed to the caller in memory.
    assert result.status is ProvenanceStatus.COMPLETE_WITH_EXCLUSIONS
    assert result.changes == ()
    assert [(change.path, change.owner) for change in caller.changes] == [
        ("own.py", "caller")
    ]


def test_post_tool_exclusion_filter_uses_the_result_snapshot_case_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: baseline and result snapshots canonicalize path case differently.
    baseline = replace(
        _snapshot(tmp_path, entries=(_entry("Peer.py", "digest:before"),)),
        is_casefolded=False,
    )
    exclusion = SnapshotExclusion(
        path="PEER.PY",
        reason="unstable_path",
        peer_agent_key="host:peer:agent",
        peer_turn_id="peer-turn",
        invocation_id="peer-open",
        started_seq=1,
        started_at="2026-07-19T01:02:03+00:00",
        observer_turn_id="caller-turn",
    )
    current = replace(
        _snapshot(tmp_path, entries=(_entry("peer.py", "digest:after"),)),
        exclusions=(exclusion,),
        is_casefolded=True,
        status=ProvenanceStatus.COMPLETE_WITH_EXCLUSIONS,
        status_reason=ProvenanceReason.PEER_ACTIVITY,
    )
    lifecycle = ProvenanceLifecycle(tmp_path)
    lifecycle._state.current = current
    lifecycle._state.turns[("caller", "caller-turn")] = TurnState(
        "caller",
        "caller-turn",
        baseline,
        0,
        True,
    )
    result = ObservationResult(
        current,
        (),
        (),
        False,
        False,
        0,
        False,
        False,
        ProvenanceStatus.COMPLETE_WITH_EXCLUSIONS,
        ProvenanceReason.PEER_ACTIVITY,
    )
    monkeypatch.setattr(lifecycle, "_observe", lambda *_args, **_kwargs: result)
    invocation = Invocation(
        "caller-post",
        "caller",
        "caller-turn",
        0,
        baseline.snapshot_id,
        frozenset({"Peer.py", "peer.py"}),
    )

    # When: replay sees delete/create deltas expressed under the old policy.
    _ = lifecycle.post_tool(invocation, "edit")

    # Then: both spellings map to the excluded result key and neither is attributed.
    assert lifecycle.changes == ()


def test_non_excluded_replay_merges_observers_without_touching_excluded_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: another observer already owns both deltas from the caller's baseline.
    (tmp_path / "own.py").write_text("before", encoding="utf-8")
    (tmp_path / "peer.py").write_text("before", encoding="utf-8")
    lifecycle = ProvenanceLifecycle(tmp_path)
    _ = lifecycle.start_turn("observer", "observer-turn")
    _ = lifecycle.start_turn("caller", "caller-turn")
    observed = lifecycle.begin_invocation(
        "observer",
        "observer-turn",
        "observer-post",
        ("own.py", "peer.py"),
    )
    (tmp_path / "own.py").write_text("after", encoding="utf-8")
    (tmp_path / "peer.py").write_text("after", encoding="utf-8")
    _ = lifecycle.post_tool(observed, "edit")
    _record_peer_window(tmp_path, "peer.py")
    caller = lifecycle.begin_invocation(
        "caller",
        "caller-turn",
        "caller-post",
        ("own.py", "peer.py"),
        prime_candidates=False,
    )
    _install_peer_exclusion_scan(
        tmp_path,
        lifecycle,
        monkeypatch,
        scan_physical=False,
    )

    # When: caller replay sees own.py but explicitly excludes peer.py.
    _ = lifecycle.post_tool(caller, "edit")

    # Then: own.py becomes contended; peer.py keeps its original attribution.
    changes = {change.path: change for change in lifecycle.changes}
    assert changes["own.py"].owner is None
    assert changes["own.py"].source == "external"
    assert changes["own.py"].attribution_status == "contended"
    assert changes["own.py"].observed_by == ("caller", "observer")
    assert changes["peer.py"].owner == "observer"
    assert changes["peer.py"].observed_by == ("observer",)


def test_post_tool_records_new_delta_beside_peer_exclusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the caller changes own.py while a peer makes peer.py unobservable.
    (tmp_path / "own.py").write_text("before", encoding="utf-8")
    (tmp_path / "peer.py").write_text("stable", encoding="utf-8")
    lifecycle = ProvenanceLifecycle(tmp_path)
    _ = lifecycle.start_turn("caller", "caller-turn")
    _record_peer_window(tmp_path, "peer.py")
    invocation = lifecycle.begin_invocation(
        "caller",
        "caller-turn",
        "caller-post",
        ("own.py", "peer.py"),
        prime_candidates=False,
    )
    (tmp_path / "own.py").write_text("after", encoding="utf-8")
    _install_peer_exclusion_scan(
        tmp_path,
        lifecycle,
        monkeypatch,
        scan_physical=True,
    )

    # When: PostTool commits a complete-with-exclusions snapshot.
    result = lifecycle.post_tool(invocation, "edit")

    # Then: the immediate result and audit state contain only own.py.
    assert result.status is ProvenanceStatus.COMPLETE_WITH_EXCLUSIONS
    assert [change.path for change in result.changes] == ["own.py"]
    assert [change.path for change in lifecycle.changes] == ["own.py"]
    assert result.snapshot is not None
    assert [item.path for item in result.snapshot.exclusions] == ["peer.py"]
    events = load_agent_events(str(tmp_path), "caller")
    assert events is not None
    committed = [
        event
        for event in events
        if event.get("event") == "change"
        and event.get("commit_state") == "committed"
    ]
    assert len(committed) == 1
    paths = committed[0]["paths"]
    assert isinstance(paths, list)
    assert [item["path"] for item in paths if isinstance(item, dict)] == ["own.py"]


@pytest.mark.parametrize("peer_timestamp", [None, "2000-01-01T00:00:00+00:00"])
def test_untrusted_peer_issue_stays_incomplete_and_skips_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    peer_timestamp: str | None,
) -> None:
    # Given: replayable deltas exist, but peer evidence is absent or expired.
    caller = _start_caller_before_shared_update(tmp_path)
    if peer_timestamp is not None:
        _record_peer_window(
            tmp_path,
            "peer.py",
            timestamp=peer_timestamp,
        )
    invocation = caller.begin_invocation(
        "caller",
        "caller-turn",
        "caller-post",
        ("own.py", "peer.py"),
        prime_candidates=False,
    )
    _install_peer_exclusion_scan(
        tmp_path,
        caller,
        monkeypatch,
        scan_physical=False,
    )

    # When: PostTool cannot validate the exclusion lease.
    result = caller.post_tool(invocation, "edit")

    # Then: it remains incomplete and records no replay attribution.
    assert result.status is ProvenanceStatus.INCOMPLETE
    assert result.incomplete is True
    assert caller.changes == ()


def test_followup_hook_preserves_turn_not_started_without_keyerror_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: turn start failed and its ledger state explicitly records a missing baseline.
    invocation = CanonicalInvocation(
        "host",
        "caller",
        "session",
        "turn",
        "edit-1",
        "pre_tool",
        "edit",
        ("app.py",),
        "",
        True,
        "",
    )
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "host": "host",
            "session_id": "session",
            "agent": "caller",
            "turn_id": "turn",
            "attribution": "exact",
            "prompt": "work",
            "baseline_snapshot_id": "snapshot:unavailable",
            "current_snapshot_id": "snapshot:unavailable",
            "provenance_incomplete": True,
            "provenance_status": "incomplete",
            "provenance_status_reason": "observation_error",
        }
    )

    class FailedBootstrapLifecycle:
        observed_file_count = 0
        current_snapshot = None

        def __init__(self, root: Path) -> None:
            self.root = root

        def resume_turn(
            self,
            agent: str,
            turn_id: str,
            mutation_capable: bool = False,
            *,
            allow_full_bootstrap: bool = False,
        ) -> None:
            raise TurnBootstrapError(
                ProvenanceStatus.INCOMPLETE,
                ProvenanceReason.OBSERVATION_ERROR,
                True,
            )

        def start_turn(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            raise TurnBootstrapError(
                ProvenanceStatus.INCOMPLETE,
                ProvenanceReason.OBSERVATION_ERROR,
                True,
            )

    # When: a later mutation-capable hook also cannot bootstrap the baseline.
    monkeypatch.setattr(adapter_observation, "ProvenanceLifecycle", FailedBootstrapLifecycle)
    report = begin_invocation(tmp_path, invocation)
    ledger = load_ledger({"project_root": str(tmp_path)})

    # Then: the same explicit degraded turn remains, with no KeyError fallback cascade.
    turn = active_turn(
        ledger,
        {
            "host": "host",
            "session_id": "session",
            "agent": "caller",
            "attribution": "exact",
        },
    )
    assert report.incomplete is True
    assert report.error_kind != "KeyError"
    assert turn is not None
    assert turn["baseline_status"] == "missing"
    assert turn["provenance_status_reason"] == "turn_not_started"
    assert turn["provenance_mutation_capable"] is True


def test_pretool_peer_rescue_atomically_recovers_missing_turn_without_posttool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: prompt bootstrap failed, while a recorded peer owns the unstable path.
    caller = {
        "project_root": str(tmp_path),
        "host": "host",
        "session_id": "caller-session",
        "agent": "caller",
        "turn_id": "caller-turn",
        "attribution": "exact",
    }
    _ = record_event(
        caller
        | {
            "event": "prompt",
            "prompt": "mutate app.py",
            "baseline_snapshot_id": "snapshot:unavailable",
            "current_snapshot_id": "snapshot:unavailable",
            "provenance_incomplete": True,
            "provenance_status": "incomplete",
            "provenance_status_reason": "observation_error",
        }
    )
    _record_peer_window(tmp_path, "hot.py")
    unstable = _snapshot(
        tmp_path,
        entries=(),
        issues=(ScanIssue("hot.py", "unstable_path"),),
    )
    monkeypatch.setattr(lifecycle_module, "scan_snapshot", lambda *_args: unstable)
    invocation = CanonicalInvocation(
        "host",
        "caller",
        "caller-session",
        "caller-turn",
        "edit-1",
        "pre_tool",
        "edit",
        ("app.py",),
        "",
        True,
        "",
    )

    # When: the mutation PreTool full bootstrap succeeds with peer exclusions.
    report = begin_invocation(tmp_path, invocation)
    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, caller)

    # Then: the owning invocation event stores baseline recovery and candidates together.
    assert report.incomplete is False
    assert report.snapshot_id
    assert turn is not None
    assert turn["baseline_status"] == "ready"
    assert turn["baseline_snapshot_id"] == report.snapshot_id
    assert turn["provenance_incomplete"] is False
    assert turn["provenance_status"] == "complete"
    assert turn["provenance_status_reason"] == ""
    invocations = turn["invocations"]
    assert isinstance(invocations, dict)
    assert invocations["edit-1"]["candidate_paths"] == ["app.py"]

    # And: even if PostToolUse fails open and records nothing, Stop is not stale-incomplete.
    decision = evaluate_without_io(ledger, caller)
    assert decision["decision"] == "allow"
    coordination = load_coordination_journal(tmp_path)
    assert coordination.complete is True
    assert [item.actor.agent_key for item in coordination.events] == [
        "host:caller-session:caller",
        "host:caller-session:caller",
    ]
    assert [item.actor_turn_id for item in coordination.events] == [
        "caller-turn",
        "caller-turn",
    ]
    assert [item.outcome for item in coordination.events] == [
        CoordinationOutcome.ENTERED,
        CoordinationOutcome.RECOVERED,
    ]
    assert [item.reason_code for item in coordination.events] == [
        CoordinationReason.TURN_NOT_STARTED,
        CoordinationReason.COMPLETE,
    ]

    # A routine later invocation must not inflate the one recovery observation.
    _ = begin_invocation(
        tmp_path,
        CanonicalInvocation(
            "host",
            "caller",
            "caller-session",
            "caller-turn",
            "edit-2",
            "pre_tool",
            "edit",
            ("later.py",),
            "",
            True,
            "",
        ),
    )
    assert len(load_coordination_journal(tmp_path).events) == 2
