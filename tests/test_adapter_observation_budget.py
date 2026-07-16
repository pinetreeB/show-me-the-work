from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.adapter_observation import (
    CanonicalInvocation,
    ObservationReport,
    _record_status,
    observe_post_tool,
    start_turn,
)
from core.ledger import load_ledger, record_event
from core.provenance_types import DEFAULT_MAX_SCAN_BYTES, ProvenanceReason, ProvenanceStatus
from core.verification_covers import active_turn


HOST = "codex_cli"
AGENT = "codex"
SESSION_ID = "session"
TURN_ID = "turn-session"


def _invocation(
    invocation_id: str, phase: str, family: str, candidates: tuple[str, ...] = ()
) -> CanonicalInvocation:
    return CanonicalInvocation(
        HOST,
        AGENT,
        SESSION_ID,
        TURN_ID,
        invocation_id,
        phase,
        family,
        candidates,
        "",
        True,
        "",
    )


def _seed_prompt_turn(root: Path) -> None:
    _ = record_event(
        {
            "project_root": str(root),
            "event": "prompt",
            "host": HOST,
            "agent": AGENT,
            "session_id": SESSION_ID,
            "turn_id": TURN_ID,
            "prompt": "work",
        }
    )


def _ledger_turn(root: Path) -> dict[str, object] | None:
    return active_turn(
        load_ledger({"project_root": str(root)}),
        {"host": HOST, "agent": AGENT, "session_id": SESSION_ID},
    )


def _trip_scope_too_large(root: Path) -> ObservationReport:
    # Given: a normal turn establishes a COMPLETE baseline over a single small file...
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    _seed_prompt_turn(root)
    started = start_turn(root, _invocation("turn-start", "turn_start", "other"))
    assert started.status is ProvenanceStatus.COMPLETE

    # ...then a nested oversized file appears before the next observation. "heavy" is a
    # sibling of "app.py" at the workspace root, so "app.py" is always reserved during the
    # root directory's single scandir pass (visiting "heavy" only pushes it onto the scan
    # stack -- it never consumes budget) before the nested breach file is ever visited,
    # regardless of OS-reported directory order.
    heavy = root / "heavy"
    heavy.mkdir()
    with (heavy / "oversized.bin").open("wb") as handle:
        handle.truncate(DEFAULT_MAX_SCAN_BYTES + 1)

    return observe_post_tool(root, _invocation("edit-1", "post_tool", "edit", ("app.py",)))


def test_observe_post_tool_scope_too_large_reports_and_persists_budget_diagnostics(
    tmp_path: Path,
) -> None:
    report = _trip_scope_too_large(tmp_path)

    # Then: the returned report carries the top path(s) and the exact breach point.
    assert report.status is ProvenanceStatus.SCOPE_TOO_LARGE
    assert report.status_reason is ProvenanceReason.BYTE_LIMIT
    assert report.budget_breach_path == "heavy/oversized.bin"
    assert len(report.budget_top_paths) == 1
    assert report.budget_top_paths[0]["path"] == "app.py"
    assert report.budget_top_paths[0]["entries"] == 1

    # And: the same diagnostics are persisted onto the ledger's active turn.
    turn = _ledger_turn(tmp_path)
    assert turn is not None
    assert turn["provenance_budget_breach_path"] == "heavy/oversized.bin"
    assert turn["provenance_budget_top_paths"] == [
        {
            "path": "app.py",
            "bytes": report.budget_top_paths[0]["bytes"],
            "entries": 1,
        }
    ]


def test_repeated_call_on_stuck_turn_reuses_persisted_diagnostics_without_rescanning(
    tmp_path: Path,
) -> None:
    first = _trip_scope_too_large(tmp_path)

    # When: another invocation observes the same already-stuck turn, the lifecycle scanner
    # must never be re-entered -- the cached ledger diagnostics are reused instead.
    with patch(
        "core.adapter_observation.ProvenanceLifecycle",
        side_effect=AssertionError("stuck turn should not re-enter the lifecycle scanner"),
    ):
        second = observe_post_tool(
            tmp_path, _invocation("edit-2", "post_tool", "edit", ("app.py",))
        )

    assert second.status is ProvenanceStatus.SCOPE_TOO_LARGE
    assert second.status_reason == first.status_reason
    assert second.budget_top_paths == first.budget_top_paths
    assert second.budget_breach_path == first.budget_breach_path


def test_record_status_clears_ledger_budget_fields_once_observation_completes(
    tmp_path: Path,
) -> None:
    # Given: a stuck observation persists non-empty budget diagnostics onto the ledger.
    _seed_prompt_turn(tmp_path)
    invocation = _invocation("edit-1", "post_tool", "edit", ("app.py",))
    stuck = ObservationReport(
        "",
        "",
        (),
        False,
        False,
        ProvenanceStatus.SCOPE_TOO_LARGE,
        ProvenanceReason.BYTE_LIMIT,
        ({"path": "heavy", "bytes": 999, "entries": 1},),
        "heavy/oversized.bin",
    )
    _record_status(tmp_path, invocation, stuck)
    turn = _ledger_turn(tmp_path)
    assert turn is not None
    assert turn["provenance_budget_top_paths"] == [{"path": "heavy", "bytes": 999, "entries": 1}]
    assert turn["provenance_budget_breach_path"] == "heavy/oversized.bin"

    # When: a later observation completes cleanly (empty budget diagnostics by construction).
    complete = ObservationReport("snapshot", "baseline", (), False, False)
    _record_status(tmp_path, invocation, complete)

    # Then: the ledger's stale budget diagnostics are cleared, not left dangling.
    turn = _ledger_turn(tmp_path)
    assert turn is not None
    assert "provenance_budget_top_paths" not in turn
    assert "provenance_budget_breach_path" not in turn
