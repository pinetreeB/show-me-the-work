from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import unittest
from collections.abc import Mapping
from unittest.mock import patch

import core.provenance_capture as capture
from core.provenance import (
    ChangeOperation,
    EntryKind,
    HASH_CHUNK_BYTES,
    ManifestEntry,
    Snapshot,
    calculate_net_delta,
    snapshot_workspace,
)


def _entries(snapshot: Snapshot) -> Mapping[str, ManifestEntry]:
    return {entry.path: entry for entry in snapshot.entries}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_snapshot_normalizes_unicode_paths_applies_policy_and_hashes_manifest(tmp_path: Path) -> None:
    # Given: hard/soft exclusions and a config that re-includes one soft-excluded file.
    _write(tmp_path / "한글 폴더" / "é space.txt", "안녕하세요")
    _write(tmp_path / ".git" / "hidden.txt", "hard")
    _write(tmp_path / "node_modules" / "ignored.js", "soft")
    _write(tmp_path / "node_modules" / "kept.js", "kept")
    _write(tmp_path / "vendor" / "hidden.txt", "config")
    _write(tmp_path / "dist" / "bundle.js", "generated")
    config = {
        "version": 1,
        "include": ["node_modules/kept.js"],
        "exclude": ["vendor/**"],
        "generated": ["dist/**"],
    }
    _write(tmp_path / ".fable-lite" / "provenance-config.json", json.dumps(config))

    # When: the project is snapshotted from its root.
    snapshot = snapshot_workspace(tmp_path)
    entries = _entries(snapshot)

    # Then: displayed paths, policy IDs, and BLAKE2b-256 entry hashes are deterministic.
    assert set(entries) == {"dist/bundle.js", "node_modules/kept.js", "한글 폴더/é space.txt"}
    assert all("\\" not in path for path in entries)
    assert entries["한글 폴더/é space.txt"].digest.startswith("blake2b-256:")
    assert snapshot.snapshot_id.startswith("blake2b-256:")
    assert snapshot.scope_policy_id.startswith("blake2b-256:")
    assert snapshot.is_generated("dist/bundle.js") is True
    assert snapshot.incomplete is False

    # And: generated attribution rules do not change the scan scope policy.
    _write(
        tmp_path / ".fable-lite" / "provenance-config.json",
        json.dumps({**config, "generated": ["out/**"]}),
    )
    generated_changed = snapshot_workspace(tmp_path)
    assert generated_changed.scope_policy_id == snapshot.scope_policy_id
    assert generated_changed.is_generated("out/bundle.js") is True


def test_net_delta_reports_create_modify_delete_type_mode_and_revert(tmp_path: Path) -> None:
    # Given: a baseline containing files for each net-delta operation.
    _write(tmp_path / "modify.txt", "before")
    _write(tmp_path / "delete.txt", "delete")
    _write(tmp_path / "type-change", "file")
    _write(tmp_path / "type-target.txt", "target")
    mode_path = tmp_path / "mode.txt"
    _write(mode_path, "mode")
    baseline = snapshot_workspace(tmp_path)

    # When: files are created, changed, deleted, converted, and permission-modified.
    _write(tmp_path / "create.txt", "create")
    _write(tmp_path / "modify.txt", "after")
    (tmp_path / "delete.txt").unlink()
    (tmp_path / "type-change").unlink()
    try:
        os.symlink("type-target.txt", tmp_path / "type-change")
    except OSError as exc:
        raise unittest.SkipTest(f"file symlink unavailable: {exc}") from exc
    original_mode = mode_path.stat().st_mode
    os.chmod(mode_path, original_mode & ~0o222)
    current = snapshot_workspace(tmp_path)
    os.chmod(mode_path, original_mode)
    operations = {delta.path: delta.op for delta in calculate_net_delta(baseline, current)}

    # Then: delta operation semantics distinguish content, type, and mode changes.
    assert operations == {
        "create.txt": ChangeOperation.CREATE,
        "delete.txt": ChangeOperation.DELETE,
        "mode.txt": ChangeOperation.MODE_CHANGE,
        "modify.txt": ChangeOperation.MODIFY,
        "type-change": ChangeOperation.TYPE_CHANGE,
    }

    # And: returning to the baseline removes the path from current pending delta.
    (tmp_path / "create.txt").unlink()
    _write(tmp_path / "delete.txt", "delete")
    (tmp_path / "type-change").unlink()
    _write(tmp_path / "type-change", "file")
    _write(tmp_path / "modify.txt", "before")
    reverted = snapshot_workspace(tmp_path)
    assert calculate_net_delta(baseline, reverted) == ()


def test_snapshot_detects_same_size_same_mtime_content_change(tmp_path: Path) -> None:
    # Given: a file with a stable baseline metadata tuple.
    path = tmp_path / "same-size.txt"
    _write(path, "abc")
    baseline = snapshot_workspace(tmp_path)
    before = path.stat()

    # When: its bytes change but size and mtime are restored exactly.
    _write(path, "xyz")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    current = snapshot_workspace(tmp_path)

    # Then: content digest detects the modification despite matching metadata.
    delta = calculate_net_delta(baseline, current)
    assert len(delta) == 1
    assert delta[0].path == "same-size.txt"
    assert delta[0].op is ChangeOperation.MODIFY


def test_snapshot_marks_path_incomplete_after_two_unstable_double_stats(
    tmp_path: Path,
) -> None:
    # Given: a regular file whose before/after metadata comparison is forced unstable.
    _write(tmp_path / "moving.txt", "moving")
    calls = 0

    def always_mismatch(left: os.stat_result, right: os.stat_result) -> bool:
        nonlocal calls
        calls += 1
        return False

    # When: the scanner performs its one permitted retry.
    with patch.object(capture, "_stats_match", side_effect=always_mismatch):
        snapshot = snapshot_workspace(tmp_path)

    # Then: no arbitrary manifest revision is emitted and the scan is incomplete.
    assert calls == 2
    assert snapshot.entries == ()
    assert snapshot.incomplete is True
    assert snapshot.issues[0].reason == "unstable_path"


def test_snapshot_hashes_large_file_in_chunks(tmp_path: Path) -> None:
    # Given: one file that crosses the public streaming-hash threshold.
    payload = b"x" * (HASH_CHUNK_BYTES + 1)
    path = tmp_path / "large.bin"
    path.write_bytes(payload)

    # When: the file is snapshotted.
    entry = _entries(snapshot_workspace(tmp_path))["large.bin"]

    # Then: its full streamed digest and size are preserved.
    expected = hashlib.blake2b(payload, digest_size=32).hexdigest()
    assert entry.size == len(payload)
    assert entry.digest == f"blake2b-256:{expected}"


def test_directory_symlink_loop_is_recorded_without_traversal(tmp_path: Path) -> None:
    # Given: a directory that links back to itself.
    loop = tmp_path / "loop"
    loop.mkdir()
    _write(loop / "inside.txt", "inside")
    try:
        os.symlink(loop, loop / "again", target_is_directory=True)
    except OSError as exc:
        raise unittest.SkipTest(f"directory symlink unavailable: {exc}") from exc

    # When: the project is scanned without following symlinks.
    entries = _entries(snapshot_workspace(tmp_path))

    # Then: the link itself is hashed and no recursive loop path is enumerated.
    assert entries["loop/again"].file_type is EntryKind.SYMLINK
    assert "loop/again/inside.txt" not in entries
    assert entries["loop/inside.txt"].file_type is EntryKind.REGULAR
