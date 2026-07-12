from __future__ import annotations

from .provenance_policy import canonical_manifest_key
from .provenance_types import (
    ChangeOperation,
    ManifestEntry,
    NetDelta,
    ProvenanceDeltaPolicyError,
    Snapshot,
)


def calculate_net_delta(baseline: Snapshot, current: Snapshot) -> tuple[NetDelta, ...]:
    before_entries = _index_entries(baseline.entries, baseline.is_casefolded)
    after_entries = _index_entries(current.entries, baseline.is_casefolded)
    deltas: list[NetDelta] = []
    for key in sorted(before_entries.keys() | after_entries.keys()):
        before = before_entries.get(key)
        after = after_entries.get(key)
        delta = _delta_for_path(key, before, after)
        if delta is not None:
            deltas.append(delta)
    return tuple(deltas)


def _index_entries(
    entries: tuple[ManifestEntry, ...],
    casefolded: bool,
) -> dict[str, ManifestEntry]:
    indexed: dict[str, ManifestEntry] = {}
    for entry in entries:
        key = canonical_manifest_key(entry.path, casefolded)
        existing = indexed.get(key)
        if existing is not None and existing.path != entry.path:
            raise ProvenanceDeltaPolicyError(key)
        indexed[key] = entry
    return indexed


def _delta_for_path(
    key: str,
    before: ManifestEntry | None,
    after: ManifestEntry | None,
) -> NetDelta | None:
    if before is None and after is not None:
        return NetDelta(after.path, key, ChangeOperation.CREATE, None, after)
    if before is not None and after is None:
        return NetDelta(before.path, key, ChangeOperation.DELETE, before, None)
    if before is None or after is None:
        return None
    if before.file_type is not after.file_type:
        return NetDelta(
            after.path,
            key,
            ChangeOperation.TYPE_CHANGE,
            before,
            after,
            before.mode != after.mode,
        )
    if before.digest != after.digest:
        return NetDelta(
            after.path,
            key,
            ChangeOperation.MODIFY,
            before,
            after,
            before.mode != after.mode,
        )
    if before.mode != after.mode:
        return NetDelta(after.path, key, ChangeOperation.MODE_CHANGE, before, after, True)
    return None
