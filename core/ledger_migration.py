from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Final
from uuid import uuid4

from .agent_log import ledger_transaction
from .ledger_schema import (
    JsonObject,
    JsonValue,
    LedgerSchemaError,
    deserialize_v2_ledger,
    serialize_v2_ledger,
)
from .ledger_storage import atomic_write_bytes, atomic_write_text, ledger_path
from .ledger_v1 import default_ledger, sequence_value
from .ledger_v2 import INVOCATION_LEASE, refresh_v1_projection


INVOCATION_STATUS_ARCHIVE_PREFIX: Final = "ledger.v2-invocation-status."
INVOCATION_STATUS_ARCHIVE_INDEX_NAME: Final = (
    "ledger.v2-invocation-status.index.json"
)
INVOCATION_STATUS_ARCHIVE_REASON: Final = "invocation_status_backfill"
INVOCATION_STATUS_ARCHIVE_MAX_COUNT: Final = 8
INVOCATION_STATUS_ARCHIVE_MAX_BYTES: Final = 16 * 1024 * 1024


class LedgerMigrationError(RuntimeError):
    def __init__(self, stage: str, detail: str) -> None:
        self.stage = stage
        self.detail = detail
        super().__init__(stage, detail)

    def __str__(self) -> str:
        return f"ledger v1 migration failed during {self.stage}: {self.detail}"


def migrate_v1_ledger(project_root: str) -> JsonObject:
    with ledger_transaction(project_root):
        return _migrate_v1_ledger(project_root)


def _migrate_v1_ledger(project_root: str) -> JsonObject:
    destination = ledger_path(project_root)
    try:
        original = destination.read_bytes()
    except OSError as exc:
        raise LedgerMigrationError("read", str(exc)) from exc
    legacy = _load_v1(original)
    if legacy is None:
        return _load_v2(original)
    archive = destination.with_name("ledger.v1.json.bak")
    archive_bytes = _preserve_archive(archive, original)
    converted = _convert_legacy(legacy)
    wrote_v2 = False
    try:
        atomic_write_text(destination, serialize_v2_ledger(converted))
        wrote_v2 = True
        restored = deserialize_v2_ledger(destination.read_text(encoding="utf-8"))
    except (OSError, LedgerSchemaError, UnicodeDecodeError) as exc:
        if wrote_v2:
            _isolate_failed(destination)
        _restore_archive(destination, archive_bytes)
        raise LedgerMigrationError("write", str(exc)) from exc
    return restored


def invocation_status_backfill_required(value: JsonValue) -> bool:
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        return False
    active_turns = value.get("active_turns")
    if not isinstance(active_turns, dict):
        return False
    for raw_turn in active_turns.values():
        if not isinstance(raw_turn, dict):
            continue
        invocations = raw_turn.get("invocations")
        if not isinstance(invocations, dict):
            continue
        if any(
            isinstance(raw_entry, dict) and "status" not in raw_entry
            for raw_entry in invocations.values()
        ):
            return True
    return False


def backfill_invocation_statuses(
    ledger: JsonObject,
    *,
    observed_at: datetime | None = None,
) -> tuple[JsonObject, int]:
    """Return a detached copy with provably expired rows closed and live rows open."""
    migrated = deepcopy(ledger)
    observed = _as_utc(observed_at or datetime.now(UTC))
    active_turns = migrated.get("active_turns")
    if not isinstance(active_turns, dict):
        return migrated, 0
    changed = 0
    for raw_turn in active_turns.values():
        if not isinstance(raw_turn, dict):
            continue
        invocations = raw_turn.get("invocations")
        if not isinstance(invocations, dict):
            continue
        for raw_entry in invocations.values():
            if isinstance(raw_entry, dict) and "status" not in raw_entry:
                status, _safe_to_persist = _missing_status_disposition(
                    raw_entry,
                    observed,
                )
                raw_entry["status"] = status
                changed += 1
    return migrated, changed


def _unsafe_invocation_status_backfills(
    ledger: JsonObject,
    observed_at: datetime,
) -> int:
    active_turns = ledger.get("active_turns")
    if not isinstance(active_turns, dict):
        return 0
    unsafe = 0
    for raw_turn in active_turns.values():
        if not isinstance(raw_turn, dict):
            continue
        invocations = raw_turn.get("invocations")
        if not isinstance(invocations, dict):
            continue
        for raw_entry in invocations.values():
            if not isinstance(raw_entry, dict) or "status" in raw_entry:
                continue
            _status, safe_to_persist = _missing_status_disposition(
                raw_entry,
                observed_at,
            )
            if not safe_to_persist:
                unsafe += 1
    return unsafe


def _missing_status_disposition(
    entry: JsonObject,
    observed_at: datetime,
) -> tuple[str, bool]:
    started_at = _parse_invocation_started_at(entry.get("started_at"))
    if started_at is None:
        return "open", False
    if observed_at - started_at > INVOCATION_LEASE:
        return "closed", True
    started_seq = entry.get("started_seq")
    candidate_paths = entry.get("candidate_paths")
    protects_candidates = (
        isinstance(started_seq, int)
        and not isinstance(started_seq, bool)
        and started_seq > 0
        and isinstance(candidate_paths, list)
        and any(isinstance(path, str) and path for path in candidate_paths)
    )
    return "open", protects_candidates


def _parse_invocation_started_at(value: JsonValue | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def migrate_v2_invocation_statuses(project_root: str) -> JsonObject:
    with ledger_transaction(project_root):
        return _migrate_v2_invocation_statuses(project_root)


def _migrate_v2_invocation_statuses(project_root: str) -> JsonObject:
    destination = ledger_path(project_root)
    try:
        original = destination.read_bytes()
    except OSError as exc:
        raise LedgerMigrationError("read", str(exc)) from exc
    raw = _load_v2_status_source(original)
    observed_at = datetime.now(UTC)
    unsafe = _unsafe_invocation_status_backfills(raw, observed_at)
    converted, changed = backfill_invocation_statuses(
        raw,
        observed_at=observed_at,
    )
    if changed == 0:
        return _load_v2(original)
    if unsafe:
        raise LedgerMigrationError(
            "classify",
            f"{unsafe} status-less invocation(s) lack complete leased R2 evidence",
        )
    try:
        serialized = serialize_v2_ledger(converted)
    except LedgerSchemaError as exc:
        raise LedgerMigrationError("validate", str(exc)) from exc
    archive_bytes, source_digest = _preserve_invocation_status_archive(
        destination,
        original,
    )
    _enforce_invocation_status_archive_retention(
        destination,
        protected_digest=source_digest,
    )
    wrote_v2 = False
    try:
        atomic_write_text(destination, serialized, prefix="ledger-status-")
        wrote_v2 = True
        restored = deserialize_v2_ledger(destination.read_text(encoding="utf-8"))
    except (OSError, LedgerSchemaError, UnicodeDecodeError) as exc:
        if wrote_v2:
            _isolate_failed(destination)
        _restore_archive(destination, archive_bytes)
        raise LedgerMigrationError("write", str(exc)) from exc
    return restored


def _load_v2_status_source(content: bytes) -> JsonObject:
    try:
        raw: JsonValue = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerMigrationError("parse", str(exc)) from exc
    if not isinstance(raw, dict):
        raise LedgerMigrationError("parse", "ledger root must be an object")
    if raw.get("schema_version") != 2:
        raise LedgerMigrationError("parse", "schema_version must equal 2")
    return raw


def _load_v1(content: bytes) -> JsonObject | None:
    try:
        raw: JsonValue = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerMigrationError("parse", str(exc)) from exc
    if not isinstance(raw, dict):
        raise LedgerMigrationError("parse", "ledger root must be an object")
    schema_version = raw.get("schema_version")
    if schema_version == 2 and not isinstance(schema_version, bool):
        return None
    if schema_version is not None and (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        raise LedgerMigrationError("parse", "schema_version must be 1 or 2")
    legacy = default_ledger()
    legacy.update(raw)
    return legacy


def _load_v2(content: bytes) -> JsonObject:
    try:
        serialized = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LedgerMigrationError("parse", str(exc)) from exc
    try:
        return deserialize_v2_ledger(serialized)
    except LedgerSchemaError as exc:
        raise LedgerMigrationError("parse", str(exc)) from exc


def _preserve_archive(
    archive: Path,
    original: bytes,
    *,
    prefix: str = "ledger-v1-",
) -> bytes:
    if archive.exists():
        try:
            archived = archive.read_bytes()
        except OSError as exc:
            raise LedgerMigrationError("archive", str(exc)) from exc
        if archived != original:
            raise LedgerMigrationError("archive", "existing archive differs from ledger.json")
        return archived
    try:
        atomic_write_bytes(archive, original, prefix=prefix)
    except OSError as exc:
        raise LedgerMigrationError("archive", str(exc)) from exc
    return original


def _preserve_invocation_status_archive(
    destination: Path,
    original: bytes,
) -> tuple[bytes, str]:
    digest = sha256(original).hexdigest()
    archive = _invocation_status_archive_path(destination, digest)
    index_path = destination.with_name(INVOCATION_STATUS_ARCHIVE_INDEX_NAME)
    index = _load_invocation_status_archive_index(index_path)
    entries = _archive_entries(index)
    archive_bytes = _preserve_archive(
        archive,
        original,
        prefix="ledger-v2-status-archive-",
    )
    existing = next(
        (
            entry
            for entry in entries
            if entry.get("digest") == digest
        ),
        None,
    )
    if existing is not None:
        if existing.get("path") != archive.name:
            raise LedgerMigrationError(
                "archive",
                f"archive index path mismatch for digest {digest}",
            )
        return archive_bytes, digest

    entries.append(
        {
            "digest": digest,
            "path": archive.name,
            "created_at": datetime.now(UTC).isoformat(),
            "reason": INVOCATION_STATUS_ARCHIVE_REASON,
        }
    )
    _write_invocation_status_archive_index(index_path, entries)
    return archive_bytes, digest


def _invocation_status_archive_path(
    destination: Path,
    digest: str,
) -> Path:
    return destination.with_name(
        f"{INVOCATION_STATUS_ARCHIVE_PREFIX}{digest}.bak"
    )


def _load_invocation_status_archive_index(path: Path) -> JsonObject:
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError:
        return {"schema_version": 1, "archives": []}
    except OSError as exc:
        raise LedgerMigrationError("archive", str(exc)) from exc
    try:
        raw: JsonValue = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerMigrationError(
            "archive",
            f"cannot parse archive index: {type(exc).__name__}",
        ) from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise LedgerMigrationError(
            "archive",
            "archive index schema_version must equal 1",
        )
    entries = raw.get("archives")
    if not isinstance(entries, list):
        raise LedgerMigrationError(
            "archive",
            "archive index archives must be a list",
        )
    seen: set[str] = set()
    normalized: list[JsonObject] = []
    for raw_entry in entries:
        entry = _validated_archive_entry(path, raw_entry)
        digest = entry["digest"]
        assert isinstance(digest, str)
        if digest in seen:
            raise LedgerMigrationError(
                "archive",
                f"duplicate archive digest {digest}",
            )
        seen.add(digest)
        normalized.append(entry)
    return {"schema_version": 1, "archives": normalized}


def _validated_archive_entry(
    index_path: Path,
    raw_entry: JsonValue,
) -> JsonObject:
    if not isinstance(raw_entry, dict):
        raise LedgerMigrationError(
            "archive",
            "archive index entry must be an object",
        )
    digest = raw_entry.get("digest")
    relative_path = raw_entry.get("path")
    created_at = raw_entry.get("created_at")
    reason = raw_entry.get("reason")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise LedgerMigrationError("archive", "archive digest must be sha256")
    expected_name = f"{INVOCATION_STATUS_ARCHIVE_PREFIX}{digest}.bak"
    if relative_path != expected_name:
        raise LedgerMigrationError(
            "archive",
            f"archive path must equal {expected_name}",
        )
    if not isinstance(created_at, str) or not created_at:
        raise LedgerMigrationError(
            "archive",
            "archive created_at must be a non-empty string",
        )
    try:
        _ = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerMigrationError(
            "archive",
            "archive created_at must be ISO-8601",
        ) from exc
    if reason != INVOCATION_STATUS_ARCHIVE_REASON:
        raise LedgerMigrationError(
            "archive",
            f"archive reason must equal {INVOCATION_STATUS_ARCHIVE_REASON}",
        )
    archive = index_path.with_name(expected_name)
    try:
        content = archive.read_bytes()
    except OSError as exc:
        raise LedgerMigrationError("archive", str(exc)) from exc
    if sha256(content).hexdigest() != digest:
        raise LedgerMigrationError(
            "archive",
            f"archive content digest mismatch for {expected_name}",
        )
    return {
        "digest": digest,
        "path": expected_name,
        "created_at": created_at,
        "reason": reason,
    }


def _archive_entries(index: JsonObject) -> list[JsonObject]:
    entries = index.get("archives")
    if not isinstance(entries, list):
        raise LedgerMigrationError(
            "archive",
            "archive index archives must be a list",
        )
    return [
        entry
        for entry in entries
        if isinstance(entry, dict)
    ]


def _write_invocation_status_archive_index(
    path: Path,
    entries: list[JsonObject],
) -> None:
    serialized = json.dumps(
        {
            "schema_version": 1,
            "archives": entries,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    try:
        atomic_write_text(
            path,
            f"{serialized}\n",
            prefix="ledger-status-index-",
        )
    except OSError as exc:
        raise LedgerMigrationError("archive", str(exc)) from exc


def _enforce_invocation_status_archive_retention(
    destination: Path,
    *,
    protected_digest: str,
) -> JsonObject:
    """MIGRATION-03: eviction is retry-safe and reports its outcomes.

    The published index stays authoritative and never references a missing
    archive (index-first rewrite, strict load contract preserved).  An archive
    whose unlink fails is left on disk as an orphan; ③ the orphan scan on this
    and later maintenance runs retries the deletion, and ④ the returned report
    surfaces every failure so it is visible instead of silently accumulating.
    """
    index_path = destination.with_name(INVOCATION_STATUS_ARCHIVE_INDEX_NAME)
    index = _load_invocation_status_archive_index(index_path)
    entries = _archive_entries(index)
    sizes: dict[str, int] = {}
    for entry in entries:
        digest = entry.get("digest")
        relative_path = entry.get("path")
        if not isinstance(digest, str) or not isinstance(relative_path, str):
            raise LedgerMigrationError("archive", "invalid archive index entry")
        try:
            sizes[digest] = destination.with_name(relative_path).stat().st_size
        except OSError as exc:
            raise LedgerMigrationError("archive", str(exc)) from exc

    # ③ Orphan discovery: digest-named archive files whose index entry is gone
    # (a past failed unlink, or a crash between index rewrite and unlink).
    # Removing them here is the retry path for failed evictions.  Legacy
    # fixed-name archives (non-digest names) are deliberately left untouched.
    indexed_names = {str(entry.get("path")) for entry in entries}
    orphans_removed: list[str] = []
    orphans_failed: list[JsonObject] = []
    try:
        siblings = list(destination.parent.iterdir())
    except OSError:
        siblings = []
    for sibling in siblings:
        name = sibling.name
        if not (
            sibling.is_file()
            and _is_digest_archive_name(name)
            and name not in indexed_names
        ):
            continue
        try:
            sibling.unlink()
        except OSError as exc:
            orphans_failed.append({"path": name, "error": type(exc).__name__})
        else:
            orphans_removed.append(name)

    retained = list(entries)
    evicted: list[JsonObject] = []
    while (
        len(retained) > INVOCATION_STATUS_ARCHIVE_MAX_COUNT
        or sum(
            sizes[str(entry["digest"])]
            for entry in retained
        )
        > INVOCATION_STATUS_ARCHIVE_MAX_BYTES
    ):
        candidates = [
            entry
            for entry in retained
            if entry.get("digest") != protected_digest
        ]
        if not candidates:
            break
        victim = min(
            candidates,
            key=lambda entry: (
                _archive_created_at(entry),
                str(entry.get("digest")),
            ),
        )
        retained.remove(victim)
        evicted.append(victim)

    evicted_paths: list[str] = []
    unlink_failed: list[JsonObject] = []
    if evicted:
        _write_invocation_status_archive_index(index_path, retained)
        for entry in evicted:
            digest = str(entry.get("digest"))
            relative_path = entry.get("path")
            if not isinstance(relative_path, str):
                unlink_failed.append({"digest": digest, "error": "invalid_path"})
                continue
            try:
                destination.with_name(relative_path).unlink(missing_ok=True)
            except OSError as exc:
                # The file stays on disk as an orphan (the index no longer
                # references it); the orphan scan above retries deletion on the
                # next maintenance run instead of losing the retry path.
                unlink_failed.append(
                    {
                        "digest": digest,
                        "path": relative_path,
                        "error": type(exc).__name__,
                    }
                )
            else:
                evicted_paths.append(relative_path)

    # ④ Retention report: every failure is visible to callers/maintenance.
    return {
        "evicted": evicted_paths,
        "failed": unlink_failed,
        "orphans_removed": orphans_removed,
        "orphans_failed": orphans_failed,
    }


def _is_digest_archive_name(name: str) -> bool:
    """True only for ``{INVOCATION_STATUS_ARCHIVE_PREFIX}<sha256>.bak`` names."""
    if not (
        name.startswith(INVOCATION_STATUS_ARCHIVE_PREFIX) and name.endswith(".bak")
    ):
        return False
    digest = name[len(INVOCATION_STATUS_ARCHIVE_PREFIX) : -len(".bak")]
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )


def _archive_created_at(entry: JsonObject) -> datetime:
    value = entry.get("created_at")
    if not isinstance(value, str):
        raise LedgerMigrationError(
            "archive",
            "archive created_at must be a string",
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerMigrationError(
            "archive",
            "archive created_at must be ISO-8601",
        ) from exc
    return _as_utc(parsed)


def _convert_legacy(legacy: JsonObject) -> JsonObject:
    turn = dict(legacy)
    _ = turn.pop("schema_version", None)
    turn["turn_id"] = "legacy-turn"
    turn["start_seq"] = sequence_value(legacy.get("event_seq"))
    turn["baseline_snapshot_id"] = "legacy:unknown"
    turn["current_snapshot_id"] = "legacy:unknown"
    turn["pending_change_ids"] = []
    turn["blocks"] = {"stop": sequence_value(legacy.get("stop_blocks"))}
    agent = legacy.get("agent")
    turn["agent"] = agent if isinstance(agent, str) and agent else "default"
    turn["migration_mode"] = "legacy_turn"
    turn["legacy_seq_less"] = _has_seq_less_success(legacy)
    migrated = dict(legacy)
    migrated["schema_version"] = 2
    migrated["manifest_generation"] = 0
    migrated["active_turns"] = {"default": turn}
    return refresh_v1_projection(migrated, turn)


def _has_seq_less_success(legacy: JsonObject) -> bool:
    results = legacy.get("verification_results")
    if not isinstance(results, list):
        return False
    return any(
        isinstance(result, dict)
        and result.get("success") is True
        and not isinstance(result.get("seq"), int)
        for result in results
    )


def _isolate_failed(destination: Path) -> None:
    if not destination.exists():
        return
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    failed = destination.with_name(f"ledger.json.migration-failed-{timestamp}-{uuid4().hex}.bak")
    try:
        os.replace(destination, failed)
    except OSError:
        return


def _restore_archive(destination: Path, archive_bytes: bytes) -> None:
    try:
        atomic_write_bytes(destination, archive_bytes, prefix="ledger-restore-")
        restored = destination.read_bytes()
    except OSError as exc:
        raise LedgerMigrationError("restore", str(exc)) from exc
    if restored != archive_bytes:
        raise LedgerMigrationError("restore", "restored bytes do not match immutable archive")
