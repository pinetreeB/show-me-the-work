from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess

from .agent_log import ledger_transaction
from .provenance import SnapshotScanOptions, snapshot_workspace_with_options
from .provenance_policy import is_hard_excluded, is_path_in_scope, load_provenance_config
from .provenance_store import SnapshotStoreError, save_turn_baseline_from_current, save_workspace_current
from .provenance_types import (
    DEFAULT_FULL_SCAN_SECONDS,
    DEFAULT_INCREMENTAL_SCAN_SECONDS,
    ProvenanceReason,
    ProvenanceStatus,
    ScanBudget,
    Snapshot,
)
from .provenance_lifecycle_types import LifecycleState


class GitTrackedPathsError(RuntimeError):
    pass


def scan_snapshot(
    root: Path,
    previous: Snapshot | None,
    forced_paths: frozenset[str],
    full_scan: bool,
) -> Snapshot:
    forced = forced_paths | persisted_force_paths(root, previous)
    try:
        forced |= tracked_force_paths(root)
    except GitTrackedPathsError:
        snapshot = snapshot_workspace_with_options(
            root,
            SnapshotScanOptions(
                previous=None if full_scan else previous,
                force_paths=forced,
            ),
        )
        return replace(
            snapshot,
            status=ProvenanceStatus.INCOMPLETE,
            status_reason=ProvenanceReason.SNAPSHOT_UNAVAILABLE,
        )
    return snapshot_workspace_with_options(
        root,
        SnapshotScanOptions(
            previous=None if full_scan else previous,
            force_paths=forced,
            budget=ScanBudget(
                max_seconds=(
                    DEFAULT_FULL_SCAN_SECONDS
                    if full_scan
                    else DEFAULT_INCREMENTAL_SCAN_SECONDS
                )
            ),
        ),
    )


def prime_candidate_scope(
    root: Path,
    state: LifecycleState,
    agent: str,
    turn_id: str,
    candidates: frozenset[str],
) -> bool:
    candidates = _new_existing_candidates(root, state.current, candidates)
    if not candidates:
        return True
    generation = state.generation
    snapshot = scan_snapshot(root, state.current, candidates, False)
    if snapshot.status is not ProvenanceStatus.COMPLETE:
        return False
    with ledger_transaction(str(root)):
        if state.generation != generation:
            return False
        turn = state.turns[(agent, turn_id)]
        before = state.current
        reconciled_at = before.full_reconciled_at if before is not None and before.snapshot_id == snapshot.snapshot_id else None
        snapshot = replace(snapshot, full_reconciled_at=reconciled_at)
        try:
            save_workspace_current(root, snapshot)
            save_turn_baseline_from_current(root, agent, turn_id, snapshot)
        except SnapshotStoreError:
            return False
        state.current = snapshot
        state.generation += 1
        state.incomplete = False
        state.current_is_stop_full = reconciled_at is not None
        state.turns[(agent, turn_id)] = replace(turn, baseline=snapshot)
        return True


def persisted_force_paths(root: Path, previous: Snapshot | None) -> frozenset[str]:
    if previous is None:
        return frozenset()
    config = load_provenance_config(root)
    return frozenset(entry.path for entry in previous.entries if not is_path_in_scope(entry.path, config))


def git_tracked_paths(root: Path) -> frozenset[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        if _has_git_metadata(root):
            raise GitTrackedPathsError(str(exc)) from exc
        return frozenset()
    if result.returncode != 0:
        if _has_git_metadata(root):
            raise GitTrackedPathsError("git ls-files failed")
        return frozenset()
    return frozenset(
        raw.decode("utf-8", errors="replace").replace("\\", "/")
        for raw in result.stdout.split(b"\0")
        if raw
    )


def tracked_force_paths(root: Path) -> frozenset[str]:
    config = load_provenance_config(root)
    return frozenset(
        path
        for path in git_tracked_paths(root)
        if not is_hard_excluded(path) and not is_path_in_scope(path, config)
    )


def _has_git_metadata(root: Path) -> bool:
    absolute = root.resolve()
    return any((candidate / ".git").exists() for candidate in (absolute, *absolute.parents))


def _new_existing_candidates(
    root: Path,
    current: Snapshot | None,
    candidates: frozenset[str],
) -> frozenset[str]:
    known = frozenset(entry.path for entry in current.entries) if current is not None else frozenset()
    return frozenset(
        candidate
        for candidate in candidates
        if candidate not in known and os.path.lexists(root / candidate)
    )
