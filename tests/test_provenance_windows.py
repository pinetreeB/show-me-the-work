from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch
import unittest

import core.provenance as provenance
from core.provenance import EntryKind, ManifestEntry, ProvenanceConfig, ScanResult, snapshot_workspace
from core.provenance_snapshot import SnapshotBuildContext, build_snapshot


@unittest.skipUnless(os.name == "nt", "Windows canonical-key contract")
def test_windows_casefold_collision_is_incomplete_without_manifest_overwrite(tmp_path: Path) -> None:
    # Given: synthetic display paths that collide only under Windows casefold keys.
    first = ManifestEntry("A.txt", "A.txt", EntryKind.REGULAR, 1, 1, 0o644, "blake2b-256:a")
    second = ManifestEntry("a.txt", "a.txt", EntryKind.REGULAR, 1, 1, 0o644, "blake2b-256:b")

    # When: the snapshot assembler applies Windows canonicalization.
    snapshot = build_snapshot(
        SnapshotBuildContext(tmp_path, ProvenanceConfig(), True),
        ScanResult((first, second), (), ()),
    )

    # Then: neither conflicting entry is retained and the reason is explicit.
    assert snapshot.entries == ()
    assert snapshot.incomplete is True
    assert snapshot.issues[0].reason == "casefold_collision"


@unittest.skipUnless(os.name == "nt", "Windows reparse-point contract")
def test_windows_reparse_file_requires_two_matching_observations(tmp_path: Path) -> None:
    # Given: one file treated as a non-symlink reparse point by the platform adapter.
    path = tmp_path / "placeholder.txt"
    path.write_text("stable", encoding="utf-8")

    # When: two identical scans occur, then the reparse bytes change.
    with patch.object(provenance, "_is_non_symlink_reparse", return_value=True):
        first = snapshot_workspace(tmp_path)
        second = snapshot_workspace(tmp_path, first)
        path.write_text("changed", encoding="utf-8")
        third = snapshot_workspace(tmp_path, second)

    # Then: first and changed observations are incomplete; only the stable pair promotes.
    assert first.entries == ()
    assert first.issues[0].reason == "unstable_reparse"
    assert second.entries[0].path == "placeholder.txt"
    assert second.incomplete is False
    assert third.entries == ()
    assert third.issues[0].reason == "unstable_reparse"
