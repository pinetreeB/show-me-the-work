from __future__ import annotations

import json
from pathlib import Path

from core.provenance import SnapshotScanOptions, snapshot_workspace, snapshot_workspace_with_options
from core.provenance_lifecycle import ProvenanceLifecycle


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _paths(root: Path) -> set[str]:
    return {entry.path for entry in snapshot_workspace(root).entries}


def test_nested_soft_patterns_match_only_contiguous_segment_chains(
    tmp_path: Path,
) -> None:
    # Given: framework caches, deployable outputs, and a non-contiguous lookalike.
    excluded = {
        "workspace/.next/cache/webpack/index.pack",
        ".turbo/state.json",
        ".parcel-cache/data.bin",
        "client/.angular/cache/compiler.bin",
        ".nuxt/nitro.json",
        ".svelte-kit/output.json",
    }
    retained = {
        ".next/server/app.js",
        ".next/static/chunk.js",
        ".next/intermediate/cache/data.bin",
        "coverage/domain-policy.json",
        "dist/bundle.js",
        "build/release.bin",
        "out/server.js",
        "target/app.jar",
    }
    for path in excluded | retained:
        _write(tmp_path / path, path)

    # When: the default product policy scans the workspace.
    observed = _paths(tmp_path)

    # Then: only exact contiguous cache chains are excluded at any depth.
    assert observed.isdisjoint(excluded)
    assert retained <= observed


def test_explicit_include_wins_over_nested_soft_exclusion(tmp_path: Path) -> None:
    # Given: one cache file is explicitly included while its sibling remains noise.
    kept = ".next/cache/webpack/keep.pack"
    skipped = ".next/cache/webpack/skip.pack"
    _write(tmp_path / kept, "keep")
    _write(tmp_path / skipped, "skip")
    _write(
        tmp_path / ".fable-lite" / "provenance-config.json",
        json.dumps({"version": 1, "include": [kept]}),
    )

    # When: the scanner applies user include before product soft defaults.
    observed = _paths(tmp_path)

    # Then: only the explicitly included cache artifact is observed.
    assert kept in observed
    assert skipped not in observed


def test_explicit_force_path_wins_over_nested_soft_exclusion(tmp_path: Path) -> None:
    # Given: an existing cache path is supplied as an explicit scanner candidate.
    forced = ".next/cache/webpack/forced.pack"
    _write(tmp_path / forced, "forced")

    # When: the scanner receives that exact force path.
    snapshot = snapshot_workspace_with_options(
        tmp_path,
        SnapshotScanOptions(force_paths=frozenset({forced})),
    )

    # Then: force-path descent and capture override the soft default.
    assert forced in {entry.path for entry in snapshot.entries}


def test_lifecycle_candidate_force_wins_over_nested_soft_exclusion(
    tmp_path: Path,
) -> None:
    # Given: a turn whose existing cache file is absent from the default baseline.
    candidate = ".next/cache/webpack/candidate.pack"
    _write(tmp_path / candidate, "candidate")
    lifecycle = ProvenanceLifecycle(tmp_path)
    started = lifecycle.start_turn("codex", "turn-1")
    assert started.snapshot is not None
    assert candidate not in {entry.path for entry in started.snapshot.entries}

    # When: a tool invocation declares that cache file as its explicit candidate.
    lifecycle.begin_invocation(
        "codex",
        "turn-1",
        "invoke-1",
        (candidate,),
    )

    # Then: candidate priming force-observes it despite the default exclusion.
    assert lifecycle._state.current is not None
    assert candidate in {entry.path for entry in lifecycle._state.current.entries}
