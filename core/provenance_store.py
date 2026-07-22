"""# noqa: SIZE_OK — snapshot exclusion persistence belongs to this card-approved store module."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import os
from pathlib import Path
import re
import tempfile
from typing import TypeAlias, override

from .agent_log import _LedgerTransaction
from .provenance_types import (
    EntryKind,
    ManifestEntry,
    ProvenanceReason,
    ProvenanceStatus,
    ScanIssue,
    Snapshot,
    SnapshotExclusion,
)
from .state_layout import state_dir, state_write_scope

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class BaselineInitialization(StrEnum):
    CREATED = "created"
    EXISTING = "existing"
    CONFLICT = "conflict"


class BaselineAdvance(StrEnum):
    COMMITTED = "committed"
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class SnapshotStoreError(ValueError):
    path: Path
    reason: str

    @override
    def __str__(self) -> str:
        return f"invalid provenance snapshot at {self.path}: {self.reason}"


def snapshots_dir(root: Path) -> Path:
    return state_dir(root) / "snapshots"


def workspace_current_path(root: Path) -> Path:
    return snapshots_dir(root) / "workspace-current.json"


def turn_baseline_path(root: Path, agent: str, turn_id: str) -> Path:
    return snapshots_dir(root) / "turns" / _safe_key(agent, "agent") / f"{_safe_key(turn_id, 'turn')}-baseline.json"


def save_workspace_current(root: Path, snapshot: Snapshot) -> Path:
    with state_write_scope(root):
        path = workspace_current_path(root)
        _save(path, snapshot)
        return path


def load_workspace_current(root: Path) -> Snapshot | None:
    return _load(workspace_current_path(root))


def save_turn_baseline(root: Path, agent: str, turn_id: str, snapshot: Snapshot) -> Path:
    with state_write_scope(root):
        path = turn_baseline_path(root, agent, turn_id)
        _save(path, snapshot, (agent, turn_id))
        return path


def save_turn_baseline_from_current(root: Path, agent: str, turn_id: str, snapshot: Snapshot) -> Path:
    with state_write_scope(root):
        destination = turn_baseline_path(root, agent, turn_id)
        _save(destination, snapshot, (agent, turn_id))
        return destination


def load_turn_baseline(root: Path, agent: str, turn_id: str) -> Snapshot | None:
    path = turn_baseline_path(root, agent, turn_id)
    return _load(path, expected_baseline_identity=(agent, turn_id))


def turn_baseline_has_identity(root: Path, agent: str, turn_id: str) -> bool:
    path = turn_baseline_path(root, agent, turn_id)
    try:
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotStoreError(path, "could not verify baseline identity") from exc
    return _baseline_has_identity(path, raw, agent, turn_id)


def initialize_turn_baseline(
    root: Path,
    agent: str,
    turn_id: str,
    expected_absent: bool,
    candidate: Snapshot,
    transaction: _LedgerTransaction,
) -> BaselineInitialization:
    resolved = root.resolve()
    transaction.assert_active_for(str(resolved))
    path = turn_baseline_path(resolved, agent, turn_id)
    try:
        existing = load_turn_baseline(resolved, agent, turn_id)
    except SnapshotStoreError:
        return BaselineInitialization.CONFLICT
    if existing is not None:
        return BaselineInitialization.EXISTING
    if not expected_absent:
        return BaselineInitialization.CONFLICT
    _save(path, candidate, (agent, turn_id))
    try:
        winner = load_turn_baseline(resolved, agent, turn_id)
    except SnapshotStoreError:
        return BaselineInitialization.CONFLICT
    return (
        BaselineInitialization.CREATED
        if winner is not None and winner.snapshot_id == candidate.snapshot_id
        else BaselineInitialization.CONFLICT
    )


def claim_legacy_turn_baseline(
    root: Path,
    agent: str,
    turn_id: str,
    expected_snapshot_id: str,
    transaction: _LedgerTransaction,
) -> bool:
    """Bind a metadata-free baseline to its identity without changing its bytes."""
    resolved = root.resolve()
    transaction.assert_active_for(str(resolved))
    path = turn_baseline_path(resolved, agent, turn_id)
    try:
        snapshot = _load(path)
        has_identity = turn_baseline_has_identity(resolved, agent, turn_id)
    except SnapshotStoreError:
        return False
    if snapshot is None or snapshot.snapshot_id != expected_snapshot_id:
        return False
    if has_identity:
        return True
    _save(path, snapshot, (agent, turn_id))
    try:
        claimed = load_turn_baseline(resolved, agent, turn_id)
    except SnapshotStoreError:
        return False
    return claimed is not None and claimed.snapshot_id == expected_snapshot_id


def advance_turn_baseline(
    root: Path,
    agent: str,
    turn_id: str,
    expected_snapshot_id: str,
    merged_snapshot: Snapshot,
    manifest_generation: int,
    transaction: _LedgerTransaction,
) -> BaselineAdvance:
    resolved = root.resolve()
    transaction.assert_active_for(str(resolved))
    from .ledger import load_ledger
    from .ledger_v1 import sequence_value

    ledger = load_ledger({"project_root": str(resolved)})
    if sequence_value(ledger.get("manifest_generation")) != manifest_generation:
        return BaselineAdvance.RETRY
    try:
        current = load_turn_baseline(resolved, agent, turn_id)
    except SnapshotStoreError:
        return BaselineAdvance.RETRY
    if current is None or current.snapshot_id != expected_snapshot_id:
        return BaselineAdvance.RETRY
    _save(
        turn_baseline_path(resolved, agent, turn_id),
        merged_snapshot,
        (agent, turn_id),
    )
    try:
        committed = load_turn_baseline(resolved, agent, turn_id)
    except SnapshotStoreError:
        return BaselineAdvance.RETRY
    return (
        BaselineAdvance.COMMITTED
        if committed is not None
        and committed.snapshot_id == merged_snapshot.snapshot_id
        else BaselineAdvance.RETRY
    )


def delete_turn_baseline(root: Path, agent: str, turn_id: str) -> None:
    with state_write_scope(root):
        path = turn_baseline_path(root, agent, turn_id)
        try:
            if path.exists():
                _assert_baseline_identity(path, agent, turn_id)
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise SnapshotStoreError(path, str(exc)) from exc


def _save(
    path: Path,
    snapshot: Snapshot,
    baseline_identity: tuple[str, str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = _to_value(snapshot)
    if baseline_identity is not None:
        value["baseline_agent"] = baseline_identity[0]
        value["baseline_turn_id"] = baseline_identity[1]
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
        prefix="snapshot-",
        suffix=".tmp",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            _ = handle.write(encoded)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SnapshotStoreError(path, str(exc)) from exc


def _safe_key(value: str, fallback: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-") or fallback


def _load(
    path: Path,
    *,
    expected_baseline_identity: tuple[str, str] | None = None,
) -> Snapshot | None:
    try:
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SnapshotStoreError(path, str(exc)) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotStoreError(path, "must be valid JSON") from exc
    snapshot = _from_value(path, raw)
    if expected_baseline_identity is not None:
        _ = _baseline_has_identity(path, raw, *expected_baseline_identity)
    return snapshot


def _assert_baseline_identity(path: Path, agent: str, turn_id: str) -> None:
    try:
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotStoreError(path, "could not verify baseline identity") from exc
    _ = _baseline_has_identity(path, raw, agent, turn_id)


def _baseline_has_identity(
    path: Path,
    raw: JsonValue,
    agent: str,
    turn_id: str,
) -> bool:
    if not isinstance(raw, dict):
        raise SnapshotStoreError(path, "must be an object")
    stored_agent = raw.get("baseline_agent")
    stored_turn = raw.get("baseline_turn_id")
    if stored_agent is None and stored_turn is None:
        return False
    if stored_agent != agent or stored_turn != turn_id:
        raise SnapshotStoreError(path, "safe-key collision with another turn baseline")
    return True


def _to_value(snapshot: Snapshot) -> dict[str, JsonValue]:
    return {
        "root": str(snapshot.root),
        "entries": [_entry_value(entry) for entry in snapshot.entries],
        "reparse_observations": [_entry_value(entry) for entry in snapshot.reparse_observations],
        "issues": [{"path": issue.path, "reason": issue.reason} for issue in snapshot.issues],
        "exclusions": [_exclusion_value(item) for item in snapshot.exclusions],
        "snapshot_id": snapshot.snapshot_id,
        "scope_policy_id": snapshot.scope_policy_id,
        "generated_patterns": list(snapshot.generated_patterns),
        "is_casefolded": snapshot.is_casefolded,
        "platform": snapshot.platform,
        "full_reconciled_at": snapshot.full_reconciled_at,
        "status": snapshot.status.value,
        "status_reason": snapshot.status_reason.value,
    }


def _entry_value(entry: ManifestEntry) -> dict[str, JsonValue]:
    return {
        "path": entry.path,
        "canonical_key": entry.canonical_key,
        "file_type": entry.file_type.value,
        "size": entry.size,
        "mtime_ns": entry.mtime_ns,
        "mode": entry.mode,
        "digest": entry.digest,
    }


def _exclusion_value(item: SnapshotExclusion) -> dict[str, JsonValue]:
    return {
        "path": item.path,
        "reason": item.reason,
        "peer_agent_key": item.peer_agent_key,
        "peer_turn_id": item.peer_turn_id,
        "invocation_id": item.invocation_id,
        "started_seq": item.started_seq,
        "started_at": item.started_at,
        "observer_turn_id": item.observer_turn_id,
    }


def _from_value(path: Path, value: JsonValue) -> Snapshot:
    if not isinstance(value, dict):
        raise SnapshotStoreError(path, "must be an object")
    entries = _entries(path, value.get("entries"), "entries")
    observations = _entries(path, value.get("reparse_observations"), "reparse_observations")
    issues = _issues(path, value.get("issues"))
    exclusions = _exclusions(path, value.get("exclusions"))
    patterns = _strings(path, value.get("generated_patterns"), "generated_patterns")
    return Snapshot(
        root=Path(_string(path, value.get("root"), "root")),
        entries=entries,
        reparse_observations=observations,
        issues=issues,
        snapshot_id=_string(path, value.get("snapshot_id"), "snapshot_id"),
        scope_policy_id=_string(path, value.get("scope_policy_id"), "scope_policy_id"),
        generated_patterns=patterns,
        exclusions=exclusions,
        is_casefolded=_boolean(path, value.get("is_casefolded"), "is_casefolded"),
        platform=_string(path, value.get("platform"), "platform"),
        full_reconciled_at=_optional_string(path, value.get("full_reconciled_at"), "full_reconciled_at"),
        status=_status(path, value.get("status"), issues),
        status_reason=_reason(path, value.get("status_reason")),
    )


def _entries(path: Path, value: JsonValue | None, field: str) -> tuple[ManifestEntry, ...]:
    if not isinstance(value, list):
        raise SnapshotStoreError(path, f"{field} must be a list")
    return tuple(_entry(path, item, field) for item in value)


def _entry(path: Path, value: JsonValue, field: str) -> ManifestEntry:
    if not isinstance(value, dict):
        raise SnapshotStoreError(path, f"{field} item must be an object")
    file_type = _string(path, value.get("file_type"), "file_type")
    try:
        kind = EntryKind(file_type)
    except ValueError as exc:
        raise SnapshotStoreError(path, "file_type is invalid") from exc
    return ManifestEntry(
        path=_string(path, value.get("path"), "path"),
        canonical_key=_string(path, value.get("canonical_key"), "canonical_key"),
        file_type=kind,
        size=_integer(path, value.get("size"), "size"),
        mtime_ns=_integer(path, value.get("mtime_ns"), "mtime_ns"),
        mode=_integer(path, value.get("mode"), "mode"),
        digest=_string(path, value.get("digest"), "digest"),
    )


def _issues(path: Path, value: JsonValue | None) -> tuple[ScanIssue, ...]:
    if not isinstance(value, list):
        raise SnapshotStoreError(path, "issues must be a list")
    issues: list[ScanIssue] = []
    for item in value:
        if not isinstance(item, dict):
            raise SnapshotStoreError(path, "issue must be an object")
        issues.append(
            ScanIssue(
                _string(path, item.get("path"), "issue.path"),
                _string(path, item.get("reason"), "issue.reason"),
            )
        )
    return tuple(issues)


def _exclusions(
    path: Path,
    value: JsonValue | None,
) -> tuple[SnapshotExclusion, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SnapshotStoreError(path, "exclusions must be a list")
    exclusions: list[SnapshotExclusion] = []
    for item in value:
        if not isinstance(item, dict):
            raise SnapshotStoreError(path, "exclusion must be an object")
        exclusions.append(
            SnapshotExclusion(
                path=_string(path, item.get("path"), "exclusion.path"),
                reason=_string(path, item.get("reason"), "exclusion.reason"),
                peer_agent_key=_string(
                    path,
                    item.get("peer_agent_key"),
                    "exclusion.peer_agent_key",
                ),
                peer_turn_id=_string(
                    path,
                    item.get("peer_turn_id"),
                    "exclusion.peer_turn_id",
                ),
                invocation_id=_string(
                    path,
                    item.get("invocation_id"),
                    "exclusion.invocation_id",
                ),
                started_seq=_integer(
                    path,
                    item.get("started_seq"),
                    "exclusion.started_seq",
                ),
                started_at=_string(
                    path,
                    item.get("started_at"),
                    "exclusion.started_at",
                ),
                observer_turn_id=_string(
                    path,
                    item.get("observer_turn_id"),
                    "exclusion.observer_turn_id",
                ),
            )
        )
    return tuple(exclusions)


def _strings(path: Path, value: JsonValue | None, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SnapshotStoreError(path, f"{field} must be a string list")
    strings = [item for item in value if isinstance(item, str)]
    if len(strings) != len(value):
        raise SnapshotStoreError(path, f"{field} must be a string list")
    return tuple(strings)


def _string(path: Path, value: JsonValue | None, field: str) -> str:
    if not isinstance(value, str):
        raise SnapshotStoreError(path, f"{field} must be a string")
    return value


def _optional_string(path: Path, value: JsonValue | None, field: str) -> str | None:
    if value is None:
        return None
    return _string(path, value, field)


def _reason(path: Path, value: JsonValue | None) -> ProvenanceReason:
    raw = _optional_string(path, value, "status_reason") or ""
    try:
        return ProvenanceReason(raw)
    except ValueError as exc:
        raise SnapshotStoreError(path, "status_reason is invalid") from exc


def _integer(path: Path, value: JsonValue | None, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SnapshotStoreError(path, f"{field} must be an integer")
    return value


def _boolean(path: Path, value: JsonValue | None, field: str) -> bool:
    if not isinstance(value, bool):
        raise SnapshotStoreError(path, f"{field} must be a boolean")
    return value


def _status(
    path: Path,
    value: JsonValue | None,
    issues: tuple[ScanIssue, ...],
) -> ProvenanceStatus:
    if value is None:
        return ProvenanceStatus.INCOMPLETE if issues else ProvenanceStatus.COMPLETE
    raw = _string(path, value, "status")
    try:
        return ProvenanceStatus(raw)
    except ValueError as exc:
        raise SnapshotStoreError(path, "status is invalid") from exc
