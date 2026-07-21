from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess

from .ledger import load_ledger
from .provenance import SnapshotScanOptions, snapshot_workspace_with_options
from .provenance_manifest import (
    BaselineUpdate,
    ManifestStoreError,
    TurnBinding,
    commit_manifest,
    load_manifest_view,
    merge_turn_baseline,
)
from .provenance_policy import (
    canonical_manifest_key,
    is_hard_excluded,
    is_path_in_scope,
    is_user_config_excluded,
    load_provenance_config,
)
from .provenance_lifecycle_start import trusted_default_policy_migration
from .provenance_store import SnapshotStoreError, load_turn_baseline
from .provenance_types import (
    DEFAULT_FULL_SCAN_SECONDS,
    DEFAULT_INCREMENTAL_SCAN_SECONDS,
    ProvenanceReason,
    ProvenanceConfig,
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
    for _ in range(2):
        try:
            view = load_manifest_view(root)
            baseline = load_turn_baseline(root, agent, turn_id)
        except (ManifestStoreError, SnapshotStoreError):
            return False
        state.current = view.snapshot
        state.generation = view.generation
        if baseline is None:
            return False
        new_candidates = _new_existing_candidates(root, baseline, candidates)
        observed_keys = _observed_candidate_keys(root, state, agent, turn_id)
        new_candidates = frozenset(
            candidate
            for candidate in new_candidates
            if canonical_manifest_key(candidate, baseline.is_casefolded)
            not in observed_keys
        )
        if not new_candidates:
            state.turns[(agent, turn_id)] = replace(
                state.turns[(agent, turn_id)],
                baseline=baseline,
            )
            return True
        snapshot = scan_snapshot(root, view.snapshot, new_candidates, False)
        if snapshot.status is not ProvenanceStatus.COMPLETE:
            return False
        turn = state.turns[(agent, turn_id)]
        before = view.snapshot
        reconciled_at = (
            before.full_reconciled_at
            if before is not None and before.snapshot_id == snapshot.snapshot_id
            else None
        )
        snapshot = replace(snapshot, full_reconciled_at=reconciled_at)
        candidate_keys = frozenset(
            canonical_manifest_key(candidate, baseline.is_casefolded)
            for candidate in new_candidates
        )
        try:
            merged_baseline = merge_turn_baseline(
                baseline,
                snapshot,
                candidate_keys,
            )
        except SnapshotStoreError:
            return False
        try:
            baseline_update = BaselineUpdate(
                agent,
                turn_id,
                baseline.snapshot_id,
                merged_baseline,
                candidate_keys,
            )
            if (agent, turn_id) in state.ledger_bound_turns:
                committed = commit_manifest(
                    root,
                    view.generation,
                    before.snapshot_id if before is not None else "snapshot:unavailable",
                    snapshot,
                    (),
                    baseline_update,
                    TurnBinding(agent, turn_id),
                )
            else:
                committed = commit_manifest(
                    root,
                    view.generation,
                    before.snapshot_id if before is not None else "snapshot:unavailable",
                    snapshot,
                    (),
                    baseline_update,
                )
        except (ManifestStoreError, SnapshotStoreError):
            return False
        if committed is None:
            continue
        state.current = committed.snapshot
        state.generation = committed.generation
        state.incomplete = False
        state.current_is_stop_full = reconciled_at is not None
        if committed.baseline is None:
            return False
        state.turns[(agent, turn_id)] = replace(turn, baseline=committed.baseline)
        return True
    return False


def persisted_force_paths(root: Path, previous: Snapshot | None) -> frozenset[str]:
    if previous is None:
        return frozenset()
    config = load_provenance_config(root)
    trusted_migration = trusted_default_policy_migration(previous, root)
    return frozenset(
        entry.path
        for entry in previous.entries
        if not is_path_in_scope(entry.path, config)
        and (not trusted_migration or is_user_config_excluded(entry.path, config))
    )


def git_tracked_paths(root: Path) -> frozenset[str]:
    head = _run_git(
        root,
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        "HEAD",
    )
    if head is None:
        return frozenset()
    if head.returncode != 0:
        if not _has_git_metadata(root):
            return frozenset()
        probe = _run_git(root, "rev-parse", "--verify", "HEAD")
        if probe is not None and probe.returncode == 0:
            raise GitTrackedPathsError("git HEAD tree discovery failed")
        head_paths = frozenset[str]()
    else:
        head_paths = _decode_git_paths(head.stdout)
    staged = _run_git(
        root,
        "diff",
        "--cached",
        "--name-only",
        "-z",
        "--diff-filter=ACMR",
    )
    if staged is None or staged.returncode != 0:
        raise GitTrackedPathsError("git tracked-path discovery failed")
    return head_paths | _decode_git_paths(staged.stdout)


def _run_git(
    root: Path,
    *args: str,
) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        if _has_git_metadata(root):
            raise GitTrackedPathsError(str(exc)) from exc
        return None


def _decode_git_paths(output: bytes) -> frozenset[str]:
    return frozenset(
        raw.decode("utf-8", errors="replace").replace("\\", "/")
        for raw in output.split(b"\0")
        if raw
    )


def tracked_force_paths(root: Path) -> frozenset[str]:
    config = _casefolded_config(load_provenance_config(root))
    return frozenset(
        path
        for path in git_tracked_paths(root)
        if not is_hard_excluded(_policy_path(path))
        and not is_path_in_scope(_policy_path(path), config)
    )


def _policy_path(path: str) -> str:
    return path.casefold() if os.name == "nt" else path


def _casefolded_config(config: ProvenanceConfig) -> ProvenanceConfig:
    if os.name != "nt":
        return config
    return ProvenanceConfig(
        include=tuple(pattern.casefold() for pattern in config.include),
        exclude=tuple(pattern.casefold() for pattern in config.exclude),
        generated=tuple(pattern.casefold() for pattern in config.generated),
        config_relative_path=config.config_relative_path.casefold(),
    )


def _has_git_metadata(root: Path) -> bool:
    absolute = root.resolve()
    return any((candidate / ".git").exists() for candidate in (absolute, *absolute.parents))


def _new_existing_candidates(
    root: Path,
    current: Snapshot | None,
    candidates: frozenset[str],
) -> frozenset[str]:
    casefolded = current.is_casefolded if current is not None else os.name == "nt"
    known = (
        frozenset(
            canonical_manifest_key(entry.path, casefolded)
            for entry in current.entries
        )
        if current is not None
        else frozenset()
    )
    return frozenset(
        candidate
        for candidate in candidates
        if canonical_manifest_key(candidate, casefolded) not in known
        and os.path.lexists(root / candidate)
    )


def _observed_candidate_keys(
    root: Path,
    state: LifecycleState,
    agent: str,
    turn_id: str,
) -> frozenset[str]:
    keys = {change.canonical_key for change in state.changes.values()}
    ledger = load_ledger({"project_root": str(root)})
    active = ledger.get("active_turns")
    turn = active.get(agent) if isinstance(active, dict) else None
    if isinstance(turn, dict) and turn.get("turn_id") == turn_id:
        revisions = turn.get("path_revisions")
        if isinstance(revisions, dict):
            keys.update(key for key in revisions if isinstance(key, str))
    return frozenset(keys)
