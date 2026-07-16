from __future__ import annotations

import os
from pathlib import Path

from .provenance import normalize_relative_path, workspace_scope_policy_id
from .provenance_policy import load_provenance_config
from .provenance_snapshot import SnapshotBuildContext, is_trusted_default_policy_migration
from .provenance_types import Snapshot


def can_fast_start(
    current: Snapshot | None,
    current_is_stop_full: bool,
    incomplete: bool,
    root: Path,
) -> bool:
    return (
        current is not None
        and current_is_stop_full
        and not incomplete
        and not current.incomplete
        and current.scope_policy_id == workspace_scope_policy_id(root)
    )


def trusted_default_policy_migration(
    current: Snapshot | None,
    root: Path,
) -> bool:
    if current is None:
        return False
    absolute_root = Path(os.path.abspath(root))
    context = SnapshotBuildContext(
        absolute_root,
        load_provenance_config(absolute_root),
        os.name == "nt",
        os.name,
    )
    return is_trusted_default_policy_migration(context, current.scope_policy_id)


def candidate_paths(root: Path, candidates: tuple[str, ...]) -> frozenset[str]:
    normalized: set[str] = set()
    for candidate in candidates:
        path = Path(candidate)
        absolute = path if path.is_absolute() else root / path
        try:
            normalized.add(normalize_relative_path(root, absolute))
        except ValueError:
            continue
    return frozenset(normalized)
