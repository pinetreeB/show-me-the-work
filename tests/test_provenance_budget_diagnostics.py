from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.provenance import ScanBudget, SnapshotScanOptions, snapshot_workspace_with_options
from core.provenance_types import ProvenanceReason, ProvenanceStatus


def _write_bytes(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"a" * size)


def test_byte_limit_scope_too_large_reports_top_paths_and_relative_breach_path(
    tmp_path: Path,
) -> None:
    # Given: two root-level siblings fit the byte budget; a nested third file breaches it.
    # (Both keep files are guaranteed to be reserved before the nested breach file is ever
    # visited, because visiting "heavy" as a directory only pushes it onto the scan stack --
    # it never consumes budget -- so "group"'s single scandir pass always finishes reserving
    # both regular-file siblings first, regardless of the OS-reported directory order.)
    _write_bytes(tmp_path / "group" / "keep1.txt", 10)
    _write_bytes(tmp_path / "group" / "keep2.txt", 20)
    _write_bytes(tmp_path / "group" / "heavy" / "breach.bin", 5000)

    snapshot = snapshot_workspace_with_options(
        tmp_path,
        SnapshotScanOptions(budget=ScanBudget(max_entries=1000, max_bytes=30, max_seconds=8.0)),
    )

    assert snapshot.status is ProvenanceStatus.SCOPE_TOO_LARGE
    assert snapshot.status_reason is ProvenanceReason.BYTE_LIMIT
    assert 1 <= len(snapshot.budget_top_paths) <= 3
    paths = {item.path: item for item in snapshot.budget_top_paths}
    assert paths["group/keep1.txt"].total_bytes == 10
    assert paths["group/keep1.txt"].total_entries == 1
    assert paths["group/keep2.txt"].total_bytes == 20
    byte_values = [item.total_bytes for item in snapshot.budget_top_paths]
    assert byte_values == sorted(byte_values, reverse=True)
    assert snapshot.budget_breach_path == "group/heavy/breach.bin"
    assert not os.path.isabs(snapshot.budget_breach_path)


def test_entry_limit_scope_too_large_reports_top_paths_and_relative_breach_path(
    tmp_path: Path,
) -> None:
    # Given: the same deterministic sibling-then-nested-breach shape, tripped by entry count.
    _write_bytes(tmp_path / "group" / "keep1.txt", 5)
    _write_bytes(tmp_path / "group" / "keep2.txt", 9)
    _write_bytes(tmp_path / "group" / "heavy" / "breach.bin", 1)

    snapshot = snapshot_workspace_with_options(
        tmp_path,
        SnapshotScanOptions(
            budget=ScanBudget(max_entries=2, max_bytes=1_000_000, max_seconds=8.0)
        ),
    )

    assert snapshot.status is ProvenanceStatus.SCOPE_TOO_LARGE
    assert snapshot.status_reason is ProvenanceReason.ENTRY_LIMIT
    assert len(snapshot.budget_top_paths) == 2
    byte_values = [item.total_bytes for item in snapshot.budget_top_paths]
    assert byte_values == sorted(byte_values, reverse=True)
    assert {item.path for item in snapshot.budget_top_paths} == {
        "group/keep1.txt",
        "group/keep2.txt",
    }
    assert snapshot.budget_breach_path == "group/heavy/breach.bin"
    assert not os.path.isabs(snapshot.budget_breach_path)


def test_deadline_exceeded_before_any_visit_has_no_breach_path_and_does_not_crash(
    tmp_path: Path,
) -> None:
    # Given: a zero-second budget trips the deadline before the first directory is popped.
    _write_bytes(tmp_path / "a.py", 7)

    snapshot = snapshot_workspace_with_options(
        tmp_path,
        SnapshotScanOptions(budget=ScanBudget(max_seconds=0.0)),
    )

    assert snapshot.status is ProvenanceStatus.SCOPE_TOO_LARGE
    assert snapshot.status_reason is ProvenanceReason.DEADLINE
    assert snapshot.budget_top_paths == ()
    assert snapshot.budget_breach_path is None


def test_deadline_exceeded_mid_scan_falls_back_to_last_visited_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: two equally-sized root-level files, and a clock that reports "on time" for the
    # first four monotonic() calls (context construction, the outer while-loop check, the
    # for-loop's pre-visit check for the first child, and _visit_path's own internal recheck
    # after stat()ing that first child) then jumps past the deadline on the fifth call (the
    # for-loop's pre-visit check for the second child). Exactly one of the two files is visited
    # and reserved before the deadline trips, regardless of which one the OS enumerates first.
    _write_bytes(tmp_path / "a.py", 7)
    _write_bytes(tmp_path / "b.py", 7)
    calls = {"n": 0}

    def fake_monotonic() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] <= 4 else 100.0

    monkeypatch.setattr("core.provenance.time.monotonic", fake_monotonic)

    snapshot = snapshot_workspace_with_options(
        tmp_path,
        SnapshotScanOptions(budget=ScanBudget(max_seconds=8.0)),
    )

    assert snapshot.status is ProvenanceStatus.SCOPE_TOO_LARGE
    assert snapshot.status_reason is ProvenanceReason.DEADLINE
    assert snapshot.budget_breach_path in {"a.py", "b.py"}
    assert not os.path.isabs(snapshot.budget_breach_path)
    assert len(snapshot.budget_top_paths) == 1
    assert snapshot.budget_top_paths[0].path == snapshot.budget_breach_path
    assert snapshot.budget_top_paths[0].total_bytes == 7


def test_complete_scan_has_empty_budget_diagnostics(tmp_path: Path) -> None:
    _write_bytes(tmp_path / "app.py", 5)

    snapshot = snapshot_workspace_with_options(tmp_path, SnapshotScanOptions())

    assert snapshot.status is ProvenanceStatus.COMPLETE
    assert snapshot.budget_top_paths == ()
    assert snapshot.budget_breach_path is None


def test_paths_deeper_than_three_segments_aggregate_into_the_shared_prefix(
    tmp_path: Path,
) -> None:
    # Given: three files sharing the "a/b/c" depth-3 prefix (one of them one level deeper).
    _write_bytes(tmp_path / "a" / "b" / "c" / "d" / "e.txt", 4)
    _write_bytes(tmp_path / "a" / "b" / "c" / "f.txt", 4)
    _write_bytes(tmp_path / "a" / "b" / "c" / "g.txt", 4)

    snapshot = snapshot_workspace_with_options(
        tmp_path,
        SnapshotScanOptions(
            budget=ScanBudget(max_entries=2, max_bytes=1_000_000, max_seconds=8.0)
        ),
    )

    assert snapshot.status is ProvenanceStatus.SCOPE_TOO_LARGE
    assert snapshot.status_reason is ProvenanceReason.ENTRY_LIMIT
    assert len(snapshot.budget_top_paths) == 1
    bucket = snapshot.budget_top_paths[0]
    assert bucket.path == "a/b/c"
    assert bucket.total_entries == 2
    assert bucket.total_bytes == 8
    assert snapshot.budget_breach_path in {
        "a/b/c/d/e.txt",
        "a/b/c/f.txt",
        "a/b/c/g.txt",
    }


def test_top_budget_paths_truncates_to_three_sorted_by_bytes_descending(
    tmp_path: Path,
) -> None:
    # Given: four distinct root-level prefixes with distinct sizes, plus a nested breach file
    # that is guaranteed to be visited only after all four siblings have been reserved.
    _write_bytes(tmp_path / "group" / "p1.txt", 10)
    _write_bytes(tmp_path / "group" / "p2.txt", 20)
    _write_bytes(tmp_path / "group" / "p3.txt", 30)
    _write_bytes(tmp_path / "group" / "p4.txt", 40)
    _write_bytes(tmp_path / "group" / "heavy" / "breach.bin", 5000)

    snapshot = snapshot_workspace_with_options(
        tmp_path,
        SnapshotScanOptions(
            budget=ScanBudget(max_entries=1000, max_bytes=100, max_seconds=8.0)
        ),
    )

    assert snapshot.status is ProvenanceStatus.SCOPE_TOO_LARGE
    assert snapshot.status_reason is ProvenanceReason.BYTE_LIMIT
    assert len(snapshot.budget_top_paths) == 3
    assert [item.path for item in snapshot.budget_top_paths] == [
        "group/p4.txt",
        "group/p3.txt",
        "group/p2.txt",
    ]
    assert [item.total_bytes for item in snapshot.budget_top_paths] == [40, 30, 20]
    assert snapshot.budget_breach_path == "group/heavy/breach.bin"
