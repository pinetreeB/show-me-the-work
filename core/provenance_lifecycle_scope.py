from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

from .agent_log import ledger_transaction
from .provenance import SnapshotScanOptions, snapshot_workspace_with_options
from .provenance_policy import is_path_in_scope, load_provenance_config
from .provenance_store import SnapshotStoreError, save_turn_baseline, save_workspace_current
from .provenance_types import Snapshot
from .provenance_lifecycle_types import LifecycleState


def scan_snapshot(
    root: Path,
    previous: Snapshot | None,
    forced_paths: frozenset[str],
    full_scan: bool,
) -> Snapshot:
    forced = forced_paths | persisted_force_paths(root, previous)
    return snapshot_workspace_with_options(
        root,
        SnapshotScanOptions(previous=None if full_scan else previous, force_paths=forced),
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
    if snapshot.incomplete:
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
            save_turn_baseline(root, agent, turn_id, snapshot)
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
