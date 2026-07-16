"""observation_error 관측성 회귀: 이벤트에 원인(issue 샘플·rebase·예외 종류)이 남아야 한다.

2026-07-16 조사에서 확정된 제3 차단 경로(동시 쓰기 → capture 불안정 → issues →
reason NONE → OBSERVATION_ERROR)가 이벤트만으로 진단 가능하도록 고정한다.
"""
from __future__ import annotations

from pathlib import Path

from core.adapter_observation import (
    ObservationReport,
    _incomplete_report,
    _issue_sample,
    _report,
)
from core.provenance_lifecycle_types import ObservationResult
from core.provenance_types import (
    ProvenanceReason,
    ProvenanceStatus,
    ScanIssue,
    Snapshot,
)


def _snapshot_with_issues(count: int) -> Snapshot:
    return Snapshot(
        root=Path("."),
        entries=(),
        reparse_observations=(),
        issues=tuple(ScanIssue(f"src/hot_{i}.py", "unstable_path") for i in range(count)),
        snapshot_id="snap-test",
        scope_policy_id="policy-test",
        generated_patterns=(),
    )


def test_issue_sample_extracts_bounded_path_reason_pairs() -> None:
    sample = _issue_sample(_snapshot_with_issues(8))
    assert len(sample) == 5
    assert sample[0] == {"path": "src/hot_0.py", "reason": "unstable_path"}


def test_issue_sample_empty_for_missing_or_clean_snapshot() -> None:
    assert _issue_sample(None) == ()
    assert _issue_sample(_snapshot_with_issues(0)) == ()


def test_incomplete_result_report_carries_issue_sample_and_rebase() -> None:
    snapshot = _snapshot_with_issues(2)
    result = ObservationResult(
        snapshot,
        (),
        (),
        True,
        True,
        1,
        False,
        False,
        ProvenanceStatus.INCOMPLETE,
        ProvenanceReason.OBSERVATION_ERROR,
    )
    report = _report(result, "")
    assert report.status_reason is ProvenanceReason.OBSERVATION_ERROR
    assert report.issue_sample == (
        {"path": "src/hot_0.py", "reason": "unstable_path"},
        {"path": "src/hot_1.py", "reason": "unstable_path"},
    )
    assert report.rebase_count == 1


def test_incomplete_report_records_error_kind() -> None:
    report = _incomplete_report(error_kind="SnapshotStoreError")
    assert report.error_kind == "SnapshotStoreError"
    assert report.status_reason is ProvenanceReason.OBSERVATION_ERROR


def test_report_defaults_keep_diagnostics_empty() -> None:
    report = ObservationReport("snap", "snap", (), False, False)
    assert report.issue_sample == ()
    assert report.rebase_count == 0
    assert report.error_kind == ""
