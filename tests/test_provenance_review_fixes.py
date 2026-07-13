from __future__ import annotations

import io
import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import core.provenance as provenance
from core.provenance import ChangeOperation, calculate_net_delta, snapshot_workspace


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_soft_excluded_directory_skips_when_include_has_no_descendant(tmp_path: Path) -> None:
    # Given: one include outside a soft-excluded dependency tree.
    _write(tmp_path / "src" / "app.ts", "app")
    node_modules = tmp_path / "node_modules"
    _write(node_modules / "pkg" / "file.js", "pkg")
    _write(
        tmp_path / ".fable-lite" / "provenance-config.json",
        json.dumps({"version": 1, "include": ["src/app.ts"]}),
    )
    scanned: set[Path] = set()
    original_scandir = provenance.os.scandir

    def observe(path: str | os.PathLike[str]) -> Iterator[os.DirEntry[str]]:
        scanned.add(Path(path))
        return original_scandir(path)

    # When: the scanner reaches the soft-excluded directory.
    with patch.object(provenance.os, "scandir", side_effect=observe):
        snapshot_workspace(tmp_path)

    # Then: it does not enter a subtree unrelated to the include pattern.
    assert node_modules not in scanned


def test_soft_excluded_directory_enters_only_include_descendant_path(tmp_path: Path) -> None:
    # Given: a soft-excluded directory with one included package and one sibling package.
    node_modules = tmp_path / "node_modules"
    included = node_modules / "pkg"
    skipped = node_modules / "other"
    _write(included / "file.js", "included")
    _write(skipped / "deep.js", "skipped")
    _write(
        tmp_path / ".fable-lite" / "provenance-config.json",
        json.dumps({"version": 1, "include": ["node_modules/pkg/file.js"]}),
    )
    scanned: set[Path] = set()
    original_scandir = provenance.os.scandir

    def observe(path: str | os.PathLike[str]) -> Iterator[os.DirEntry[str]]:
        scanned.add(Path(path))
        return original_scandir(path)

    # When: the scanner resolves the precise included descendant.
    with patch.object(provenance.os, "scandir", side_effect=observe):
        snapshot = snapshot_workspace(tmp_path)

    # Then: it enters only the needed branch and retains its included file.
    assert node_modules in scanned
    assert included in scanned
    assert skipped not in scanned
    assert {entry.path for entry in snapshot.entries} == {"node_modules/pkg/file.js"}


def test_nested_soft_excluded_directory_is_skipped_at_any_depth(tmp_path: Path) -> None:
    dependency_root = tmp_path / "workspace" / "node_modules"
    _write(dependency_root / "pkg" / "index.js", "ignored")
    _write(tmp_path / "workspace" / "src" / "app.js", "tracked")
    scanned: set[Path] = set()
    original_scandir = provenance.os.scandir

    def observe(path: str | os.PathLike[str]) -> Iterator[os.DirEntry[str]]:
        scanned.add(Path(path))
        return original_scandir(path)

    with patch.object(provenance.os, "scandir", side_effect=observe):
        snapshot = snapshot_workspace(tmp_path)

    assert dependency_root not in scanned
    assert {entry.path for entry in snapshot.entries} == {"workspace/src/app.js"}


def test_nested_soft_exclude_allows_only_explicit_include_descendant(
    tmp_path: Path,
) -> None:
    dependency_root = tmp_path / "workspace" / "node_modules"
    included = dependency_root / "pkg"
    skipped = dependency_root / "other"
    _write(included / "index.js", "included")
    _write(skipped / "index.js", "skipped")
    _write(
        tmp_path / ".fable-lite" / "provenance-config.json",
        json.dumps(
            {"version": 1, "include": ["workspace/node_modules/pkg/index.js"]}
        ),
    )
    scanned: set[Path] = set()
    original_scandir = provenance.os.scandir

    def observe(path: str | os.PathLike[str]) -> Iterator[os.DirEntry[str]]:
        scanned.add(Path(path))
        return original_scandir(path)

    with patch.object(provenance.os, "scandir", side_effect=observe):
        snapshot = snapshot_workspace(tmp_path)

    assert included in scanned
    assert skipped not in scanned
    assert {entry.path for entry in snapshot.entries} == {
        "workspace/node_modules/pkg/index.js"
    }


def test_previous_matching_metadata_reuses_digest_without_opening_content(tmp_path: Path) -> None:
    # Given: a completed prior manifest for one unchanged regular file.
    target = tmp_path / "stable.txt"
    _write(target, "stable")
    previous = snapshot_workspace(tmp_path)

    # When: metadata fast-path scans with that prior manifest.
    with patch.object(io, "open", wraps=io.open) as opener:
        current = snapshot_workspace(tmp_path, previous)
    target_open_calls = len(
        [call for call in opener.call_args_list if call.args and call.args[0] == target]
    )

    # Then: the file's previous digest is reused without a content open.
    assert target_open_calls == 0
    assert current.entries == previous.entries


def test_full_hash_uses_fd_stats_for_regular_file_consistency(tmp_path: Path) -> None:
    # Given: a cold scan of one regular file without a prior manifest.
    _write(tmp_path / "stable.txt", "stable")

    # When: the scanner performs its full content hash.
    with patch.object(os, "fstat", wraps=os.fstat) as fstat:
        snapshot_workspace(tmp_path)

    # Then: descriptor metadata brackets the streaming content read.
    assert fstat.call_count >= 2


def test_delta_rekeys_current_manifest_to_baseline_casefold_policy(tmp_path: Path) -> None:
    # Given: an unchanged path observed first with Windows casefold keys.
    _write(tmp_path / "ReadMe.TXT", "stable")
    baseline = snapshot_workspace(tmp_path, windows=True)

    # When: the same workspace resumes under POSIX key policy.
    current = snapshot_workspace(tmp_path, windows=False)
    delta = calculate_net_delta(baseline, current)

    # Then: policy metadata is retained and no create/delete storm is emitted.
    assert delta == ()
    assert baseline.is_casefolded is True
    assert current.is_casefolded is False
    assert baseline.platform == os.name
    assert current.platform == os.name


def test_modify_delta_preserves_mode_change_audit_flag(tmp_path: Path) -> None:
    # Given: a writable script in a baseline manifest.
    path = tmp_path / "script.py"
    _write(path, "before")
    baseline = snapshot_workspace(tmp_path)
    original_mode = path.stat().st_mode

    # When: one operation changes both content and the writable mode.
    _write(path, "after!")
    os.chmod(path, original_mode & ~0o222)
    try:
        delta = calculate_net_delta(baseline, snapshot_workspace(tmp_path))[0]
    finally:
        os.chmod(path, original_mode)

    # Then: modify remains the primary operation while the mode fact is explicit.
    assert delta.op is ChangeOperation.MODIFY
    assert delta.before is not None
    assert delta.after is not None
    assert delta.before.mode != delta.after.mode
    assert delta.mode_changed is True
