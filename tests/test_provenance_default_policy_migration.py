from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from core.provenance import (
    ChangeOperation,
    Snapshot,
    SnapshotScanOptions,
    snapshot_workspace_with_options,
    workspace_scope_policy_id,
)
from core.provenance_lifecycle import ProvenanceLifecycle
from core.provenance_policy import load_provenance_config
from core.provenance_snapshot import SnapshotBuildContext
import core.provenance_snapshot as provenance_snapshot
from core.provenance_store import save_turn_baseline, save_workspace_current


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")


def _save_legacy_current(root: Path, *forced_paths: str) -> Snapshot:
    snapshot = snapshot_workspace_with_options(
        root,
        SnapshotScanOptions(force_paths=frozenset(forced_paths)),
    )
    context = SnapshotBuildContext(
        root.resolve(),
        load_provenance_config(root),
        snapshot.is_casefolded,
        snapshot.platform,
    )
    legacy_policy_id = provenance_snapshot._scope_policy_id_for_revision(
        context,
        None,
    )
    legacy = replace(
        snapshot,
        scope_policy_id=legacy_policy_id,
        full_reconciled_at="2026-07-16T00:00:00+00:00",
    )
    save_workspace_current(root, legacy)
    return legacy


def test_default_policy_revision_changes_id_and_forces_full_turn_start(
    tmp_path: Path,
) -> None:
    # Given: a reusable Stop snapshot under the current product default policy.
    _write(tmp_path / "app.py", "stable")
    lifecycle = ProvenanceLifecycle(tmp_path)
    lifecycle.start_turn("codex", "turn-1")
    lifecycle.finish_turn("codex", "turn-1")

    # When: the product default-policy revision advances without a user config edit.
    with patch.object(
        provenance_snapshot,
        "DEFAULT_POLICY_REVISION",
        provenance_snapshot.DEFAULT_POLICY_REVISION + 1,
    ):
        result = ProvenanceLifecycle(tmp_path).start_turn("codex", "turn-2")
        revised_policy_id = workspace_scope_policy_id(tmp_path)

    # Then: the policy ID mismatch disables the metadata-only turn-start fast path.
    assert result.full_scan is True
    assert result.snapshot is not None
    assert result.snapshot.scope_policy_id == revised_policy_id


def test_trusted_default_migration_drops_existing_untracked_cache(
    tmp_path: Path,
) -> None:
    # Given: a legacy current that observed a now-default-excluded untracked cache.
    cache_path = ".next/cache/webpack/index.pack"
    _write(tmp_path / cache_path, "cache")
    _write(tmp_path / "app.py", "source")
    _save_legacy_current(tmp_path, cache_path)

    # When: turn start migrates that legacy current to the revised product policy.
    result = ProvenanceLifecycle(tmp_path).start_turn("codex", "turn-1")

    # Then: the untracked cache is absent from the new current baseline.
    assert result.snapshot is not None
    assert {entry.path for entry in result.snapshot.entries} == {"app.py"}


def test_trusted_default_migration_keeps_git_tracked_cache(tmp_path: Path) -> None:
    # Given: the same newly default-excluded cache path is committed to Git.
    cache_path = ".next/cache/webpack/index.pack"
    _init_repo(tmp_path)
    _write(tmp_path / cache_path, "tracked cache")
    _write(tmp_path / "app.py", "source")
    _git(tmp_path, "add", "-f", cache_path, "app.py")
    _git(tmp_path, "commit", "-qm", "tracked cache fixture")
    _save_legacy_current(tmp_path, cache_path)

    # When: the default-policy migration scans with the Git backstop enabled.
    result = ProvenanceLifecycle(tmp_path).start_turn("codex", "turn-1")

    # Then: tracked force wins over the new product default exclusion.
    assert result.snapshot is not None
    assert cache_path in {entry.path for entry in result.snapshot.entries}


def test_user_config_exclusion_still_forces_existing_path_observation(
    tmp_path: Path,
) -> None:
    # Given: a prior current contains an ordinary path before a user config change.
    target = tmp_path / "vendor" / "cache.bin"
    _write(target, "before")
    lifecycle = ProvenanceLifecycle(tmp_path)
    lifecycle.start_turn("codex", "turn-1")
    lifecycle.finish_turn("codex", "turn-1")

    # When: the user excludes and mutates that pre-existing path between turns.
    _write(
        tmp_path / ".fable-lite" / "provenance-config.json",
        json.dumps({"version": 1, "exclude": ["vendor/**"]}),
    )
    _write(target, "after")
    result = lifecycle.start_turn("codex", "turn-2")

    # Then: user-config self-exclusion cannot hide the physical modification.
    assert result.snapshot is not None
    assert "vendor/cache.bin" in {entry.path for entry in result.snapshot.entries}
    assert any(
        change.path == "vendor/cache.bin" and change.op is ChangeOperation.MODIFY
        for change in result.changes
    )


def test_trusted_policy_scope_removal_is_not_recorded_as_physical_delete(
    tmp_path: Path,
) -> None:
    # Given: a legacy current whose only policy-sensitive entry still exists on disk.
    cache_path = ".next/cache/webpack/index.pack"
    _write(tmp_path / cache_path, "cache")
    _write(tmp_path / "app.py", "source")
    legacy = _save_legacy_current(tmp_path, cache_path)
    save_turn_baseline(tmp_path, "codex", "turn-legacy", legacy)

    # When: a persisted active turn reconciles under the revised policy.
    lifecycle = ProvenanceLifecycle(tmp_path)
    lifecycle.resume_turn("codex", "turn-legacy")
    result = lifecycle.reconcile_turn("codex", "turn-legacy")

    # Then: scope removal is not emitted as a physical DELETE observation.
    assert all(
        not (change.path == cache_path and change.op is ChangeOperation.DELETE)
        for change in result.changes
    )
    assert lifecycle.changes == ()
    assert result.pending_change_ids == ()
