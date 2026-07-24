"""# noqa: SIZE_OK — W3 must extend this existing adapter-core boundary without new production modules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from .adapter_change_events import record_observed_changes
from .ledger import (
    JsonObject,
    JsonValue,
    capture_verification_covers,
    load_ledger,
    record_event_if_current_turn,
)
from .provenance_manifest import (
    ManifestStoreError,
    TurnBootstrapStatus,
    TurnEventCommit,
    TurnEventCommitStatus,
    ensure_turn_bootstrap,
    record_turn_event_if_ready,
)
from .provenance_policy import (
    PROJECT_PATH_IN_ROOT,
    canonicalize_project_logical_path,
    canonicalize_project_path,
)
from .provenance_lifecycle import ProvenanceLifecycle
from .provenance_progress import scan_progress
from .provenance_lifecycle_types import ObservationResult, ObservedChange
from .ledger_v2 import turn_is_closed
from .project_root import is_user_home_root
from .provenance_store import SnapshotStoreError
from .provenance_turn_resume import MissingTurnBaselineError, TurnBootstrapError
from .provenance_types import (
    ProvenanceReason,
    ProvenanceStatus,
    Snapshot,
    normalize_budget_breach_path,
    normalize_budget_top_paths,
)
from .shell_hints import shell_candidate_paths
from .shell_command import (
    ShellClassification,
    ShellEffect,
    classify_shell_effect,
    is_remote_mutation_command,
)
from .verification import is_verification_command
from .verification_covers import active_turn


OBSERVABLE_FAMILIES: Final = frozenset({"edit", "shell"})
INVOCATION_COMMIT_RETRIES: Final = 8


@dataclass(frozen=True, slots=True)
class CanonicalInvocation:
    host: str
    agent: str
    session_id: str
    turn_id: str
    invocation_id: str
    phase: str
    tool_family_hint: str
    candidate_paths: tuple[str, ...]
    command_hint: str
    success: bool
    evidence: str
    identity_synthetic: bool = False
    turn_synthetic: bool = False
    identity_conflict: bool = False

    @property
    def agent_key(self) -> str:
        return f"{self.host}:{self.session_id}:{self.agent}"

    @property
    def scorecard_attribution(self) -> str:
        return "legacy_default" if self.identity_synthetic else "exact"

    @property
    def mutation_capable(self) -> bool:
        return _mutation_capable(self)

    def as_dict(self) -> JsonObject:
        return {
            "host": self.host,
            "agent": self.agent,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "invocation_id": self.invocation_id,
            "phase": self.phase,
            "tool_family_hint": self.tool_family_hint,
            "candidate_paths": list(self.candidate_paths),
            "command_hint": self.command_hint,
            "success": self.success,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class ObservationReport:
    snapshot_id: str
    baseline_snapshot_id: str
    changed_paths: tuple[str, ...]
    incomplete: bool
    full_reconcile: bool
    status: ProvenanceStatus = ProvenanceStatus.COMPLETE
    status_reason: ProvenanceReason = ProvenanceReason.NONE
    budget_top_paths: tuple[JsonObject, ...] = ()
    budget_breach_path: str | None = None
    issue_sample: tuple[JsonObject, ...] = ()
    rebase_count: int = 0
    error_kind: str = ""


def start_turn(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    if report := _home_root_unsupported_report(root):
        return report
    try:
        lifecycle = ProvenanceLifecycle(root)
        with scan_progress(lifecycle.observed_file_count):
            result = lifecycle.start_turn(
                invocation.agent_key,
                invocation.turn_id,
                event_agent=invocation.agent,
                host=invocation.host,
                session_id=invocation.session_id,
                invocation_id=invocation.invocation_id,
                observed_at=invocation.phase,
            )
    except (KeyError, OSError, SnapshotStoreError) as exc:
        return _incomplete_report(error_kind=type(exc).__name__)
    baseline_snapshot_id = next(
        (
            turn.baseline.snapshot_id
            for turn in lifecycle.active_turns
            if turn.agent == invocation.agent_key and turn.turn_id == invocation.turn_id
        ),
        "",
    )
    return _report(result, baseline_snapshot_id)


def begin_invocation(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    if report := _home_root_unsupported_report(root):
        _record_invocation(root, invocation, _covers(root, invocation))
        return report
    invocation = _with_shell_candidates(_active_invocation(root, invocation))
    if invocation.identity_conflict:
        return ObservationReport(
            "",
            "",
            (),
            True,
            False,
            ProvenanceStatus.INCOMPLETE,
            ProvenanceReason.TURN_NOT_STARTED,
            error_kind="StaleTurn",
        )
    if report := _scope_too_large_report(root, invocation):
        _record_invocation(root, invocation, _covers(root, invocation))
        return report
    baseline_missing_hint = _baseline_missing(root, invocation)
    try:
        lifecycle = ProvenanceLifecycle(root)
        bootstrap_identity = _ledger_payload(root, invocation)
        if _mutation_capable(invocation):
            bootstrap_identity["provenance_mutation_capable"] = True
        bootstrap = ensure_turn_bootstrap(
            root,
            bootstrap_identity,
            lifecycle.current_snapshot,
            baseline_missing_hint=baseline_missing_hint,
        )
        if bootstrap.status is TurnBootstrapStatus.CANDIDATE_REQUIRED:
            with scan_progress(lifecycle.observed_file_count):
                prepared = lifecycle.start_turn(
                    invocation.agent_key,
                    invocation.turn_id,
                    _mutation_capable(invocation),
                    event_agent=invocation.agent,
                    host=invocation.host,
                    session_id=invocation.session_id,
                    invocation_id=invocation.invocation_id,
                    observed_at=invocation.phase,
                )
            if prepared.incomplete or prepared.snapshot is None:
                report = _report(prepared, "")
                _record_status(root, invocation, report)
                return report
            bootstrap = ensure_turn_bootstrap(
                root,
                bootstrap_identity,
                prepared.snapshot,
                baseline_missing_hint=baseline_missing_hint,
            )
        if bootstrap.status is TurnBootstrapStatus.STALE_TURN:
            return ObservationReport(
                "",
                "",
                (),
                True,
                False,
                ProvenanceStatus.INCOMPLETE,
                ProvenanceReason.TURN_NOT_STARTED,
                error_kind="StaleTurn",
            )
        if (
            bootstrap.status is TurnBootstrapStatus.DEGRADED
            or bootstrap.baseline is None
        ):
            report = ObservationReport(
                "",
                "",
                (),
                True,
                False,
                ProvenanceStatus.INCOMPLETE,
                ProvenanceReason.BASELINE_STATE_MISMATCH,
                error_kind="BaselineStateMismatch",
            )
            _record_status(root, invocation, report)
            return report
        lifecycle = ProvenanceLifecycle(root)
        lifecycle.resume_turn(
            invocation.agent_key,
            invocation.turn_id,
            _mutation_capable(invocation),
            require_ledger_turn=True,
        )
        with scan_progress(lifecycle.observed_file_count):
            started = lifecycle.begin_invocation(
                invocation.agent_key,
                invocation.turn_id,
                invocation.invocation_id,
                _logical_candidate_paths(root, invocation.candidate_paths),
                event_agent=invocation.agent,
                host=invocation.host,
                session_id=invocation.session_id,
                observed_at=invocation.phase,
            )
        if lifecycle.incomplete:
            report = _incomplete_report(error_kind="CandidateBaselineAdvanceError")
            _record_status(root, invocation, report)
            return report
        covers = _covers(root, invocation)
    except TurnBootstrapError as exc:
        report = ObservationReport(
            "",
            "",
            (),
            exc.incomplete,
            True,
            exc.status,
            exc.reason,
        )
        _record_status(root, invocation, report)
        return report
    except (KeyError, OSError, ManifestStoreError, SnapshotStoreError) as exc:
        report = _incomplete_report(error_kind=type(exc).__name__)
        _record_status(root, invocation, report)
        return report
    persisted_baseline_id = next(
        (
            turn.baseline.snapshot_id
            for turn in lifecycle.active_turns
            if turn.agent == invocation.agent_key and turn.turn_id == invocation.turn_id
        ),
        "",
    )
    commit = (
        _record_invocation(root, invocation, covers, persisted_baseline_id)
        if persisted_baseline_id
        else TurnEventCommit(TurnEventCommitStatus.DEGRADED)
    )
    if commit.status is not TurnEventCommitStatus.RECORDED:
        if commit.status is TurnEventCommitStatus.STALE_TURN:
            return ObservationReport(
                "",
                "",
                (),
                True,
                False,
                ProvenanceStatus.INCOMPLETE,
                ProvenanceReason.TURN_NOT_STARTED,
                error_kind="StaleTurn",
            )
        if commit.status is TurnEventCommitStatus.DEGRADED:
            try:
                final_bootstrap = ensure_turn_bootstrap(
                    root,
                    bootstrap_identity,
                    None,
                    require_existing_turn=True,
                )
            except ManifestStoreError:
                final_bootstrap = None
            if (
                final_bootstrap is not None
                and final_bootstrap.status is TurnBootstrapStatus.STALE_TURN
            ):
                return ObservationReport(
                    "",
                    "",
                    (),
                    True,
                    False,
                    ProvenanceStatus.INCOMPLETE,
                    ProvenanceReason.TURN_NOT_STARTED,
                    error_kind="StaleTurn",
                )
            reason = ProvenanceReason.BASELINE_STATE_MISMATCH
            error_kind = "InvocationBaselineRace"
        else:
            reason = ProvenanceReason.OBSERVATION_ERROR
            error_kind = "InvocationCommitRetryExhausted"
        report = ObservationReport(
            "",
            "",
            (),
            True,
            False,
            ProvenanceStatus.INCOMPLETE,
            reason,
            error_kind=error_kind,
        )
        _record_status(root, invocation, report)
        return report
    return ObservationReport(started.snapshot_id, "", (), False, False)


def observe_post_tool(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    if report := _home_root_unsupported_report(root):
        _record_status(root, invocation, report)
        return report
    invocation = _with_shell_candidates(_active_invocation(root, invocation))
    if report := _scope_too_large_report(root, invocation):
        _record_status(root, invocation, report)
        return report
    try:
        lifecycle = ProvenanceLifecycle(root)
        lifecycle.resume_turn(
            invocation.agent_key,
            invocation.turn_id,
            _mutation_capable(invocation),
            require_ledger_turn=True,
        )
        started = lifecycle.begin_invocation(
            invocation.agent_key,
            invocation.turn_id,
            invocation.invocation_id,
            _stored_candidates(root, invocation),
            False,
            event_agent=invocation.agent,
            host=invocation.host,
            session_id=invocation.session_id,
            observed_at=invocation.phase,
        )
        with scan_progress(lifecycle.observed_file_count):
            result = lifecycle.post_tool(started, _source(invocation))
    except TurnBootstrapError as exc:
        report = _turn_bootstrap_error_report(exc)
        _record_status(root, invocation, report)
        return report
    except (KeyError, OSError, SnapshotStoreError) as exc:
        report = _incomplete_report(error_kind=type(exc).__name__)
        _record_status(root, invocation, report)
        return report
    report = _report(result, "")
    _record_changes(root, invocation, result.changes, report.snapshot_id)
    _record_status(root, invocation, report)
    return report


def finish_turn(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    if report := _home_root_unsupported_report(root):
        return report
    invocation = _active_invocation(root, invocation)
    _record_finish_requested(root, invocation)
    if report := _scope_too_large_report(root, invocation):
        return report
    try:
        lifecycle = ProvenanceLifecycle(root)
        lifecycle.resume_turn(
            invocation.agent_key,
            invocation.turn_id,
            _mutation_capable(invocation),
            require_ledger_turn=True,
        )
        with scan_progress(lifecycle.observed_file_count):
            result = lifecycle.reconcile_turn(
                invocation.agent_key,
                invocation.turn_id,
                event_agent=invocation.agent,
                host=invocation.host,
                session_id=invocation.session_id,
                invocation_id=invocation.invocation_id,
                observed_at=invocation.phase,
            )
    except TurnBootstrapError as exc:
        report = _turn_bootstrap_error_report(exc, True)
        _record_status(root, invocation, report)
        return report
    except MissingTurnBaselineError:
        ledger = load_ledger({"project_root": str(root)})
        turns = ledger.get("active_turns")
        if not isinstance(turns, dict) or not isinstance(
            turns.get(invocation.agent_key), dict
        ):
            return ObservationReport("", "", (), False, True)
        report = _incomplete_report(True, error_kind="MissingTurnBaselineError")
        _record_status(root, invocation, report)
        return report
    except KeyError:
        return ObservationReport("", "", (), False, True)
    except (OSError, SnapshotStoreError) as exc:
        report = _incomplete_report(True, error_kind=type(exc).__name__)
        _record_status(root, invocation, report)
        return report
    report = _report(result, "")
    _record_changes(root, invocation, result.changes, report.snapshot_id)
    _record_status(root, invocation, report)
    return report


def reconcile_turn(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    if report := _home_root_unsupported_report(root):
        return report
    invocation = _active_invocation(root, invocation)
    if report := _scope_too_large_report(root, invocation):
        return report
    try:
        lifecycle = ProvenanceLifecycle(root)
        lifecycle.resume_turn(
            invocation.agent_key,
            invocation.turn_id,
            _mutation_capable(invocation),
            require_ledger_turn=True,
        )
        with scan_progress(lifecycle.observed_file_count):
            result = lifecycle.reconcile_turn(
                invocation.agent_key,
                invocation.turn_id,
                event_agent=invocation.agent,
                host=invocation.host,
                session_id=invocation.session_id,
                invocation_id=invocation.invocation_id,
                observed_at=invocation.phase,
            )
    except TurnBootstrapError as exc:
        report = _turn_bootstrap_error_report(exc, True)
        _record_status(root, invocation, report)
        return report
    except (KeyError, OSError, SnapshotStoreError) as exc:
        report = _incomplete_report(True, error_kind=type(exc).__name__)
        _record_status(root, invocation, report)
        return report
    report = _report(result, "")
    _record_changes(root, invocation, result.changes, report.snapshot_id)
    _record_status(root, invocation, report)
    return report


def verification_covers(root: Path, invocation: CanonicalInvocation) -> JsonObject | None:
    resolved = _active_invocation(root, invocation)
    return _stored_covers(root, resolved) or _covers(root, resolved)


def resolve_active_invocation(root: Path, invocation: CanonicalInvocation) -> CanonicalInvocation:
    return _active_invocation(root, invocation)


def record_r2_deny_after_resolution(
    root: Path,
    invocation: CanonicalInvocation,
    coordination_reason_code: str,
) -> bool:
    """Resolve the audit identity after R2 decides, without affecting that decision."""
    try:
        from .scorecard import Attribution, SessionIdentity
        from .scorecard_coordination import (
            CoordinationCategory,
            CoordinationOutcome,
            CoordinationReason,
            new_coordination_event,
            record_r2_deny_coordination,
            stable_coordination_event_id,
        )

        resolved = resolve_active_invocation(root, invocation)
        actor = SessionIdentity(
            resolved.host,
            resolved.session_id,
            resolved.agent,
        )
        reason = CoordinationReason(coordination_reason_code)
        evidence_refs = (
            (f"invocation:{resolved.invocation_id}",)
            if resolved.invocation_id
            else ()
        )
        attribution = (
            Attribution.LEGACY_DEFAULT
            if resolved.scorecard_attribution == Attribution.LEGACY_DEFAULT.value
            else Attribution.EXACT
        )
        event_id = stable_coordination_event_id(
            root,
            actor,
            resolved.turn_id,
            CoordinationCategory.R2_DENY,
            CoordinationOutcome.BLOCKED,
            reason,
            evidence_refs,
        )
        event = new_coordination_event(
            actor,
            resolved.turn_id,
            CoordinationCategory.R2_DENY,
            CoordinationOutcome.BLOCKED,
            reason,
            evidence_refs=evidence_refs,
            attribution=attribution,
            event_id=event_id,
            occurred_at=_r2_coordination_time(root, resolved),
        )
        return record_r2_deny_coordination(root, event)
    except Exception:  # noqa: BLE001 - audit I/O cannot undo an R2 denial.
        return False


def _r2_coordination_time(
    root: Path,
    invocation: CanonicalInvocation,
) -> datetime | None:
    ledger = load_ledger({"project_root": str(root)})
    turn = active_turn(ledger, _ledger_payload(root, invocation))
    if turn is None:
        return None
    candidates: list[JsonValue] = []
    invocations = turn.get("invocations")
    if isinstance(invocations, dict):
        raw_invocation = invocations.get(invocation.invocation_id)
        if isinstance(raw_invocation, dict):
            candidates.append(raw_invocation.get("started_at"))
    candidates.append(turn.get("started_at"))
    for value in candidates:
        if not isinstance(value, str):
            continue
        try:
            observed = datetime.fromisoformat(value)
        except ValueError:
            continue
        if observed.tzinfo is not None and observed.utcoffset() == UTC.utcoffset(observed):
            return observed.astimezone(UTC)
    return None


def restart_blocked_turn(root: Path, invocation: CanonicalInvocation) -> None:
    ledger = load_ledger({"project_root": str(root)})
    turns = ledger.get("active_turns")
    turn = turns.get(invocation.agent_key) if isinstance(turns, dict) else None
    if (
        not isinstance(turn, dict)
        or turn.get("turn_id") != invocation.turn_id
    ):
        return
    _ = start_turn(root, invocation)


def _report(result: ObservationResult, baseline_snapshot_id: str) -> ObservationReport:
    if result.incomplete:
        return ObservationReport(
            "",
            "",
            (),
            True,
            result.full_scan,
            result.status,
            result.status_reason,
            issue_sample=_issue_sample(result.snapshot),
            rebase_count=result.rebase_count,
        )
    if result.status is ProvenanceStatus.SCOPE_TOO_LARGE:
        top_paths, breach_path = _snapshot_budget_fields(result.snapshot)
        return ObservationReport(
            "",
            "",
            (),
            False,
            result.full_scan,
            result.status,
            result.status_reason,
            top_paths,
            breach_path,
        )
    snapshot = result.snapshot
    snapshot_id = snapshot.snapshot_id if snapshot is not None else ""
    return ObservationReport(
        snapshot_id,
        baseline_snapshot_id or snapshot_id,
        tuple(change.path for change in result.changes),
        result.incomplete,
        result.full_scan,
        result.status,
        result.status_reason,
    )


def _snapshot_budget_fields(snapshot: Snapshot | None) -> tuple[tuple[JsonObject, ...], str | None]:
    top_paths = getattr(snapshot, "budget_top_paths", None)
    breach_path = getattr(snapshot, "budget_breach_path", None)
    if not top_paths:
        return (), breach_path if isinstance(breach_path, str) and breach_path else None
    return (
        tuple(
            {"path": item.path, "bytes": item.total_bytes, "entries": item.total_entries}
            for item in top_paths
        ),
        breach_path if isinstance(breach_path, str) and breach_path else None,
    )


def _incomplete_report(full_reconcile: bool = False, error_kind: str = "") -> ObservationReport:
    return ObservationReport(
        "",
        "",
        (),
        True,
        full_reconcile,
        ProvenanceStatus.INCOMPLETE,
        ProvenanceReason.OBSERVATION_ERROR,
        error_kind=error_kind,
    )


def _issue_sample(snapshot: Snapshot | None, limit: int = 5) -> tuple[JsonObject, ...]:
    if snapshot is None or not snapshot.issues:
        return ()
    return tuple(
        {"path": issue.path, "reason": issue.reason} for issue in snapshot.issues[:limit]
    )


def _home_root_unsupported_report(root: Path) -> ObservationReport | None:
    if not is_user_home_root(root):
        return None
    return ObservationReport(
        "",
        "",
        (),
        False,
        False,
        ProvenanceStatus.UNSUPPORTED,
        ProvenanceReason.HOME_ROOT,
    )


def _covers(root: Path, invocation: CanonicalInvocation) -> JsonObject | None:
    if not is_verification_command(invocation.command_hint):
        return None
    try:
        return capture_verification_covers(
            _ledger_payload(root, invocation)
            | {"remote_target_ids": list(_remote_target_ids(invocation))}
        )
    except ValueError:
        return None


def _record_invocation(
    root: Path,
    invocation: CanonicalInvocation,
    covers: JsonObject | None,
    baseline_snapshot_id: str = "",
) -> TurnEventCommit:
    logical_candidates = _logical_candidate_paths(root, invocation.candidate_paths)
    resolved_candidates = _resolved_candidate_paths(root, invocation.candidate_paths)
    payload = _ledger_payload(root, invocation) | {
        "event": "invocation",
        # candidate_paths remains the resolved compatibility projection for
        # pre-ATTR-02 readers.  New readers use the two explicit fields.
        "candidate_paths": list(resolved_candidates),
        "candidate_logical_paths": list(logical_candidates),
        "candidate_resolved_paths": list(resolved_candidates),
    }
    classification = _shell_classification(invocation)
    if _mutation_capable(invocation, classification):
        payload["provenance_mutation_capable"] = True
    target_ids = _remote_target_ids(invocation, classification)
    if target_ids and not is_verification_command(invocation.command_hint):
        payload["provenance_remote_mutation"] = True
        payload["remote_target_ids"] = list(target_ids)
    if covers is not None:
        payload["covers"] = covers
    if baseline_snapshot_id:
        payload["baseline_status"] = "ready"
        expected = baseline_snapshot_id
        for _attempt in range(INVOCATION_COMMIT_RETRIES):
            payload["baseline_snapshot_id"] = expected
            committed = record_turn_event_if_ready(root, payload, expected)
            if committed.status is not TurnEventCommitStatus.RETRY:
                return committed
            if not committed.baseline_snapshot_id:
                return committed
            expected = committed.baseline_snapshot_id
        return TurnEventCommit(TurnEventCommitStatus.RETRY, expected)
    return TurnEventCommit(
        TurnEventCommitStatus.RECORDED
        if record_event_if_current_turn(payload, allow_missing=True)
        else TurnEventCommitStatus.STALE_TURN
    )


def _logical_candidate_paths(
    root: Path,
    candidates: tuple[str, ...],
) -> tuple[str, ...]:
    return _candidate_keys(root, candidates, resolve=False)


def _resolved_candidate_paths(
    root: Path,
    candidates: tuple[str, ...],
) -> tuple[str, ...]:
    return _candidate_keys(root, candidates, resolve=True)


def _candidate_keys(
    root: Path,
    candidates: tuple[str, ...],
    *,
    resolve: bool,
) -> tuple[str, ...]:
    normalized: dict[str, None] = {}
    for candidate in candidates:
        canonicalize = (
            canonicalize_project_path
            if resolve
            else canonicalize_project_logical_path
        )
        disposition, canonical = canonicalize(root, candidate)
        if disposition != PROJECT_PATH_IN_ROOT or canonical is None:
            continue
        normalized.setdefault(canonical, None)
    return tuple(normalized)


def _record_finish_requested(root: Path, invocation: CanonicalInvocation) -> None:
    if load_ledger({"project_root": str(root)}).get("schema_version") != 2:
        return
    _ = record_event_if_current_turn(
        _ledger_payload(root, invocation)
        | {
            "event": "finish_requested",
        }
    )


def _record_changes(
    root: Path,
    invocation: CanonicalInvocation,
    changes: tuple[ObservedChange, ...],
    snapshot_id: str,
) -> None:
    if not changes:
        return
    record_observed_changes(
        _ledger_payload(root, invocation),
        invocation.invocation_id,
        invocation.phase,
        changes,
        snapshot_id,
    )


def _record_status(root: Path, invocation: CanonicalInvocation, report: ObservationReport) -> None:
    classification = _shell_classification(invocation)
    payload = _ledger_payload(root, invocation) | {
        "event": "observation",
        "current_snapshot_id": report.snapshot_id,
        "provenance_incomplete": report.incomplete,
        "provenance_status": report.status.value,
        "provenance_status_reason": report.status_reason,
        "provenance_budget_top_paths": list(report.budget_top_paths),
        "provenance_budget_breach_path": report.budget_breach_path,
    }
    if report.issue_sample:
        payload["provenance_issue_sample"] = list(report.issue_sample)
    if report.rebase_count:
        payload["provenance_rebase_count"] = report.rebase_count
    if report.error_kind:
        payload["provenance_error_kind"] = report.error_kind
    if report.status_reason is ProvenanceReason.BASELINE_STATE_MISMATCH:
        payload["baseline_status"] = "degraded"
    if _mutation_capable(invocation, classification):
        payload["provenance_mutation_capable"] = True
    target_ids = _remote_target_ids(invocation, classification)
    if target_ids and not is_verification_command(invocation.command_hint):
        payload["provenance_remote_mutation"] = True
        payload["remote_target_ids"] = list(target_ids)
    _ = record_event_if_current_turn(payload)


def _ledger_payload(root: Path, invocation: CanonicalInvocation) -> JsonObject:
    return {
        "project_root": str(root),
        "host": invocation.host,
        "agent": invocation.agent,
        "session_id": invocation.session_id,
        "turn_id": invocation.turn_id,
        "invocation_id": invocation.invocation_id,
        "attribution": invocation.scorecard_attribution,
    }


def _active_invocation(root: Path, invocation: CanonicalInvocation) -> CanonicalInvocation:
    ledger = load_ledger({"project_root": str(root)})
    turns = ledger.get("active_turns")
    if not isinstance(turns, dict):
        return invocation
    exact = turns.get(invocation.agent_key)
    if isinstance(exact, dict):
        turn_id = exact.get("turn_id")
        if (
            isinstance(turn_id, str)
            and turn_id
            and (turn_id == invocation.turn_id or invocation.turn_synthetic)
        ):
            return replace(invocation, turn_id=turn_id, turn_synthetic=False)
        if (
            isinstance(turn_id, str)
            and turn_id
            and not turn_is_closed(ledger, invocation.agent_key, invocation.turn_id)
        ):
            return replace(invocation, turn_id=turn_id, turn_synthetic=False)
        return replace(invocation, identity_conflict=True)
    if turn_is_closed(ledger, invocation.agent_key, invocation.turn_id):
        return replace(invocation, identity_conflict=True)
    if not invocation.identity_synthetic:
        return invocation
    matches = [
        (key, candidate)
        for key, candidate in turns.items()
        if isinstance(key, str)
        and isinstance(candidate, dict)
        and key.startswith(f"{invocation.host}:")
        and candidate.get("agent") == invocation.agent
    ]
    if len(matches) != 1:
        return invocation
    key, candidate = matches[0]
    parts = key.split(":", 2)
    turn_id = candidate.get("turn_id")
    if len(parts) != 3 or not isinstance(turn_id, str) or not turn_id:
        return invocation
    if (
        turn_id != invocation.turn_id
        and not invocation.turn_synthetic
        and (
            turn_is_closed(ledger, key, invocation.turn_id)
            or _turn_closed_for_host_agent(ledger, invocation)
        )
    ):
        return replace(invocation, identity_conflict=True)
    return replace(
        invocation,
        session_id=parts[1],
        turn_id=turn_id,
        identity_synthetic=False,
        turn_synthetic=False,
    )


def _turn_closed_for_host_agent(
    ledger: JsonObject,
    invocation: CanonicalInvocation,
) -> bool:
    closed = ledger.get("closed_turns")
    if not isinstance(closed, list):
        return False
    for item in closed:
        if not isinstance(item, dict) or item.get("turn_id") != invocation.turn_id:
            continue
        key = item.get("agent_key")
        if not isinstance(key, str):
            continue
        parts = key.split(":", 2)
        if (
            len(parts) == 3
            and parts[0] == invocation.host
            and parts[2] == invocation.agent
        ):
            return True
    return False


def _baseline_missing(root: Path, invocation: CanonicalInvocation) -> bool:
    turn = active_turn(
        load_ledger({"project_root": str(root)}),
        _ledger_payload(root, invocation),
    )
    return turn is not None and turn.get("baseline_status") == "missing"


def _turn_bootstrap_error_report(
    error: TurnBootstrapError,
    full_reconcile: bool = False,
) -> ObservationReport:
    return ObservationReport(
        "",
        "",
        (),
        True,
        full_reconcile,
        error.status,
        error.reason,
        error_kind="TurnBootstrapError",
    )


def _with_shell_candidates(invocation: CanonicalInvocation) -> CanonicalInvocation:
    if invocation.tool_family_hint != "shell":
        return invocation
    candidates = tuple(dict.fromkeys((*invocation.candidate_paths, *shell_candidate_paths(invocation.command_hint))))
    return replace(invocation, candidate_paths=candidates)


def _stored_candidates(root: Path, invocation: CanonicalInvocation) -> tuple[str, ...]:
    turn = active_turn(load_ledger({"project_root": str(root)}), _ledger_payload(root, invocation))
    if turn is None:
        return invocation.candidate_paths
    invocations = turn.get("invocations")
    if not isinstance(invocations, dict):
        return invocation.candidate_paths
    stored = invocations.get(invocation.invocation_id)
    if not isinstance(stored, dict):
        return invocation.candidate_paths
    # ATTR-03: PostTool candidate match는 logical key OR invocation-time resolved
    # key — symlink replacement는 logical로, write-through는 resolved로 매칭된다.
    # 어느 쪽만 보면 write-through delta가 external로 오귀속된다(INV: attribution
    # evidence는 실제 물리 write를 따라야 한다).
    paths: list[str] = []
    logical = stored.get("candidate_logical_paths")
    if isinstance(logical, list):
        paths.extend(path for path in logical if isinstance(path, str))
    resolved = stored.get("candidate_resolved_paths")
    if isinstance(resolved, list):
        paths.extend(path for path in resolved if isinstance(path, str))
    if not paths:
        # Live ledgers written before ATTR-02 have only candidate_paths.  Their
        # historical resolved projection is the best available PostTool filter.
        legacy = stored.get("candidate_paths")
        if isinstance(legacy, list):
            paths.extend(path for path in legacy if isinstance(path, str))
    if not paths:
        return invocation.candidate_paths
    return tuple(dict.fromkeys(paths))


def _stored_covers(root: Path, invocation: CanonicalInvocation) -> JsonObject | None:
    turn = active_turn(load_ledger({"project_root": str(root)}), _ledger_payload(root, invocation))
    if turn is None:
        return None
    invocations = turn.get("invocations")
    if not isinstance(invocations, dict):
        return None
    stored = invocations.get(invocation.invocation_id)
    if not isinstance(stored, dict):
        return None
    covers = stored.get("covers")
    return covers if isinstance(covers, dict) else None


def _source(invocation: CanonicalInvocation) -> str:
    return invocation.tool_family_hint if invocation.tool_family_hint in OBSERVABLE_FAMILIES else "external"


def _mutation_capable(
    invocation: CanonicalInvocation,
    classification: ShellClassification | None = None,
) -> bool:
    if invocation.tool_family_hint == "edit":
        return True
    if invocation.tool_family_hint != "shell":
        return False
    resolved = classification or classify_shell_effect(invocation.command_hint)
    return resolved.effect is ShellEffect.LOCAL_OR_UNKNOWN


def _remote_mutation(invocation: CanonicalInvocation) -> bool:
    return (
        invocation.tool_family_hint == "shell"
        and is_remote_mutation_command(invocation.command_hint)
    )


def _shell_classification(invocation: CanonicalInvocation) -> ShellClassification | None:
    if invocation.tool_family_hint != "shell":
        return None
    return classify_shell_effect(invocation.command_hint)


def _remote_target_ids(
    invocation: CanonicalInvocation,
    classification: ShellClassification | None = None,
) -> tuple[str, ...]:
    if not _remote_mutation(invocation):
        return ()
    resolved = classification or classify_shell_effect(invocation.command_hint)
    return resolved.remote_target_ids


def _scope_too_large_report(
    root: Path,
    invocation: CanonicalInvocation,
) -> ObservationReport | None:
    turn = active_turn(load_ledger({"project_root": str(root)}), _ledger_payload(root, invocation))
    if turn is None or turn.get("provenance_status") != ProvenanceStatus.SCOPE_TOO_LARGE.value:
        return None
    reason = turn.get("provenance_status_reason")
    top_paths = cast(
        tuple[JsonObject, ...],
        normalize_budget_top_paths(turn.get("provenance_budget_top_paths")),
    )
    breach_path = normalize_budget_breach_path(turn.get("provenance_budget_breach_path"))
    return ObservationReport(
        "",
        "",
        (),
        False,
        False,
        ProvenanceStatus.SCOPE_TOO_LARGE,
        ProvenanceReason(reason)
        if isinstance(reason, str) and reason in ProvenanceReason
        else ProvenanceReason.NONE,
        top_paths,
        breach_path,
    )
