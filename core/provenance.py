from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import stat
import time
from typing import Final

from .provenance_capture import (
    HASH_CHUNK_BYTES,
    CaptureRequest,
    CapturedPath,
    capture_regular,
    capture_regular_many,
    capture_symlink,
)
from .provenance_delta import calculate_net_delta
from .provenance_policy import (
    canonical_manifest_key,
    is_hard_excluded,
    is_path_in_scope,
    load_provenance_config,
    normalize_relative_path,
    should_descend,
)
from .provenance_snapshot import SnapshotBuildContext, build_snapshot, scope_policy_id
from .provenance_types import (
    ChangeOperation,
    EntryKind,
    ManifestEntry,
    NetDelta,
    ProvenanceConfig,
    ProvenanceReason,
    ScanBudgetPath,
    ScanIssue,
    ScanResult,
    Snapshot,
    SnapshotScanOptions,
    ProvenanceStatus,
    ScanBudget,
)

REPARSE_ATTRIBUTE: Final = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
__all__ = (
    "ChangeOperation",
    "EntryKind",
    "HASH_CHUNK_BYTES",
    "ManifestEntry",
    "NetDelta",
    "ProvenanceConfig",
    "ScanBudgetPath",
    "ScanIssue",
    "ScanResult",
    "Snapshot",
    "SnapshotScanOptions",
    "calculate_net_delta",
    "canonical_manifest_key",
    "load_provenance_config",
    "normalize_relative_path",
    "snapshot_workspace",
    "snapshot_workspace_with_options",
    "workspace_scope_policy_id",
)


@dataclass(frozen=True, slots=True)
class _ScanContext:
    root: Path
    config: ProvenanceConfig
    windows: bool
    force_keys: frozenset[str]
    previous_entries: dict[str, ManifestEntry]
    previous_reparse_observations: dict[str, ManifestEntry]
    budget: ScanBudget
    deadline: float


@dataclass(frozen=True, slots=True)
class _PathInfo:
    path: Path
    relative: str
    key: str
    metadata: os.stat_result
    is_link: bool


class _ScanState:
    """Accumulates one directory walk because discovery order requires mutable work lists."""

    directories: list[tuple[Path, str]]
    entries: list[ManifestEntry]
    observations: list[ManifestEntry]
    issues: list[ScanIssue]
    regular_requests: list[CaptureRequest]
    status: ProvenanceStatus
    status_reason: ProvenanceReason
    entry_count: int
    byte_count: int
    budget_bytes: dict[str, int]
    budget_entries: dict[str, int]
    budget_breach_path: str | None
    last_relative: str

    def __init__(self, root: Path) -> None:
        self.directories = [(root, "")]
        self.entries = []
        self.observations = []
        self.issues = []
        self.regular_requests = []
        self.status = ProvenanceStatus.COMPLETE
        self.status_reason = ProvenanceReason.NONE
        self.entry_count = 0
        self.byte_count = 0
        self.budget_bytes = {}
        self.budget_entries = {}
        self.budget_breach_path = None
        self.last_relative = ""


def snapshot_workspace(
    root: Path,
    previous: Snapshot | None = None,
    windows: bool | None = None,
) -> Snapshot:
    return snapshot_workspace_with_options(
        root,
        SnapshotScanOptions(previous=previous, windows=windows),
    )


def snapshot_workspace_with_options(root: Path, options: SnapshotScanOptions) -> Snapshot:
    absolute_root = Path(os.path.abspath(root))
    budget = options.budget or ScanBudget()
    casefolded = os.name == "nt" if options.windows is None else options.windows
    previous_entries, previous_reparse_observations = _previous_state(
        options.previous,
        options.windows,
    )
    context = _ScanContext(
        root=absolute_root,
        config=load_provenance_config(absolute_root),
        windows=casefolded,
        force_keys=frozenset(
            canonical_manifest_key(path, casefolded)
            for path in options.force_paths
        ),
        previous_entries=previous_entries,
        previous_reparse_observations=previous_reparse_observations,
        budget=budget,
        deadline=time.monotonic() + max(0.0, budget.max_seconds),
    )
    scan_result = _scan(context)
    snapshot = build_snapshot(
        SnapshotBuildContext(context.root, context.config, context.windows, os.name),
        scan_result,
    )
    if scan_result.budget_top_paths or scan_result.budget_breach_path:
        snapshot = replace(
            snapshot,
            budget_top_paths=scan_result.budget_top_paths,
            budget_breach_path=scan_result.budget_breach_path,
        )
    return snapshot


def workspace_scope_policy_id(root: Path, windows: bool | None = None) -> str:
    absolute_root = Path(os.path.abspath(root))
    casefolded = os.name == "nt" if windows is None else windows
    context = SnapshotBuildContext(
        absolute_root,
        load_provenance_config(absolute_root),
        casefolded,
        os.name,
    )
    return scope_policy_id(context)


def _scan(context: _ScanContext) -> ScanResult:
    state = _ScanState(context.root)
    while state.directories and state.status is ProvenanceStatus.COMPLETE:
        if _deadline_exceeded(context, state):
            break
        directory, parent_relative = state.directories.pop()
        try:
            with os.scandir(directory) as scan:
                for child in scan:
                    if _deadline_exceeded(context, state):
                        break
                    relative = f"{parent_relative}/{child.name}" if parent_relative else child.name
                    _visit_path(child, relative, context, state)
                    if state.status is not ProvenanceStatus.COMPLETE:
                        break
        except OSError:
            state.issues.append(ScanIssue(parent_relative, "unreadable_directory"))
            continue
    if state.status is ProvenanceStatus.SCOPE_TOO_LARGE:
        return ScanResult(
            (), (), (), state.status, state.status_reason,
            _top_budget_paths(state), state.budget_breach_path,
        )
    for captured in capture_regular_many(tuple(state.regular_requests), context.deadline):
        if captured.status_reason:
            return ScanResult(
                (),
                (),
                (),
                ProvenanceStatus.SCOPE_TOO_LARGE,
                captured.status_reason,
                _top_budget_paths(state),
                state.budget_breach_path,
            )
        _append_capture(captured, state)
    if _deadline_exceeded(context, state):
        return ScanResult(
            (), (), (), state.status, state.status_reason,
            _top_budget_paths(state), state.budget_breach_path,
        )
    return ScanResult(
        tuple(state.entries),
        tuple(state.observations),
        tuple(state.issues),
        state.status,
        state.status_reason,
    )


def _visit_path(
    child: os.DirEntry[str],
    relative: str,
    context: _ScanContext,
    state: _ScanState,
) -> None:
    state.last_relative = relative
    try:
        metadata = child.stat(follow_symlinks=False)
        is_link = child.is_symlink()
    except OSError:
        state.issues.append(ScanIssue(relative, "unreadable_path"))
        return
    if _deadline_exceeded(context, state):
        return
    mode = metadata.st_mode
    if not is_link and not _is_non_symlink_reparse(metadata, context.windows):
        if stat.S_ISDIR(mode):
            if should_descend(relative, context.config) or _has_forced_descendant(relative, context):
                state.directories.append((Path(child.path), relative))
            return
        if stat.S_ISREG(mode):
            if not _in_scope(relative, context):
                return
            if not _reserve_entry(metadata.st_size, relative, context, state):
                return
            key = relative.casefold() if context.windows else relative
            previous = None if key in context.force_keys else context.previous_entries.get(key)
            state.regular_requests.append(CaptureRequest(Path(child.path), relative, key, metadata, previous))
            return
    info = _PathInfo(
        Path(child.path),
        relative,
        canonical_manifest_key(relative, context.windows),
        metadata,
        is_link,
    )
    if info.is_link:
        if not _in_scope(relative, context):
            return
        if not _reserve_entry(metadata.st_size, relative, context, state):
            return
        _append_capture(capture_symlink(_capture_request(info)), state)
        return
    if _is_non_symlink_reparse(metadata, context.windows):
        _visit_reparse(info, context, state)
        return
    state.issues.append(ScanIssue(relative, "special_file"))


def _visit_reparse(
    info: _PathInfo,
    context: _ScanContext,
    state: _ScanState,
) -> None:
    if not _in_scope(info.relative, context):
        return
    if stat.S_ISDIR(info.metadata.st_mode):
        state.issues.append(ScanIssue(info.relative, "unstable_reparse"))
        return
    if not _reserve_entry(info.metadata.st_size, info.relative, context, state):
        return
    captured = capture_regular(_capture_request(info), context.deadline)
    if captured.entry is None:
        _append_capture(captured, state)
        return
    state.observations.append(captured.entry)
    previous = context.previous_reparse_observations.get(info.key)
    if previous is not None and previous.digest == captured.entry.digest:
        state.entries.append(captured.entry)
        return
    state.issues.append(ScanIssue(info.relative, "unstable_reparse"))


def _append_capture(
    captured: CapturedPath,
    state: _ScanState,
) -> None:
    if captured.status_reason:
        state.status = ProvenanceStatus.SCOPE_TOO_LARGE
        state.status_reason = captured.status_reason
        return
    if captured.entry is not None:
        state.entries.append(captured.entry)
    if captured.issue is not None:
        state.issues.append(captured.issue)


def _deadline_exceeded(context: _ScanContext, state: _ScanState) -> bool:
    if context.budget.max_seconds > 0.0 and time.monotonic() <= context.deadline:
        return False
    state.status = ProvenanceStatus.SCOPE_TOO_LARGE
    state.status_reason = ProvenanceReason.DEADLINE
    if state.budget_breach_path is None:
        state.budget_breach_path = state.last_relative or None
    return True


def _budget_prefix(relative: str, max_depth: int = 3) -> str:
    segments = relative.split("/")
    return "/".join(segments[:max_depth])


def _accumulate_budget_prefix(state: _ScanState, relative: str, size: int) -> None:
    prefix = _budget_prefix(relative)
    state.budget_bytes[prefix] = state.budget_bytes.get(prefix, 0) + size
    state.budget_entries[prefix] = state.budget_entries.get(prefix, 0) + 1


def _top_budget_paths(state: _ScanState, limit: int = 3) -> tuple[ScanBudgetPath, ...]:
    ranked = sorted(
        state.budget_bytes.keys(),
        key=lambda prefix: (
            -state.budget_bytes[prefix],
            -state.budget_entries.get(prefix, 0),
            prefix,
        ),
    )
    return tuple(
        ScanBudgetPath(prefix, state.budget_bytes[prefix], state.budget_entries.get(prefix, 0))
        for prefix in ranked[:limit]
    )


def _reserve_entry(size: int, relative: str, context: _ScanContext, state: _ScanState) -> bool:
    if state.entry_count + 1 > context.budget.max_entries:
        state.status = ProvenanceStatus.SCOPE_TOO_LARGE
        state.status_reason = ProvenanceReason.ENTRY_LIMIT
        state.budget_breach_path = relative
        return False
    if state.byte_count + max(0, size) > context.budget.max_bytes:
        state.status = ProvenanceStatus.SCOPE_TOO_LARGE
        state.status_reason = ProvenanceReason.BYTE_LIMIT
        state.budget_breach_path = relative
        return False
    state.entry_count += 1
    state.byte_count += max(0, size)
    _accumulate_budget_prefix(state, relative, max(0, size))
    return True


def _capture_request(
    info: _PathInfo,
    previous: ManifestEntry | None = None,
) -> CaptureRequest:
    return CaptureRequest(info.path, info.relative, info.key, info.metadata, previous)


def _in_scope(relative: str, context: _ScanContext) -> bool:
    return (
        is_path_in_scope(relative, context.config)
        or canonical_manifest_key(relative, context.windows) in context.force_keys
    )


def _has_forced_descendant(relative: str, context: _ScanContext) -> bool:
    prefix = f"{canonical_manifest_key(relative, context.windows)}/"
    return not is_hard_excluded(relative) and any(
        key.startswith(prefix) for key in context.force_keys
    )


def _previous_state(
    snapshot: Snapshot | None,
    windows: bool | None,
) -> tuple[dict[str, ManifestEntry], dict[str, ManifestEntry]]:
    casefolded = os.name == "nt" if windows is None else windows
    if snapshot is None or snapshot.is_casefolded != casefolded:
        return {}, {}
    return (
        {entry.canonical_key: entry for entry in snapshot.entries},
        {entry.canonical_key: entry for entry in snapshot.reparse_observations},
    )


def _is_non_symlink_reparse(metadata: os.stat_result, windows: bool) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    return windows and bool(attributes & REPARSE_ATTRIBUTE)
