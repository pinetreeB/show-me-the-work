from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from uuid import uuid4

from .agent_log import ledger_transaction
from .ledger_schema import JsonObject, JsonValue, LedgerSchemaError, deserialize_v2_ledger, serialize_v2_ledger
from .ledger_storage import atomic_write_bytes, atomic_write_text, ledger_path
from .ledger_v1 import default_ledger, sequence_value
from .ledger_v2 import refresh_v1_projection


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


def _preserve_archive(archive: Path, original: bytes) -> bytes:
    if archive.exists():
        try:
            archived = archive.read_bytes()
        except OSError as exc:
            raise LedgerMigrationError("archive", str(exc)) from exc
        if archived != original:
            raise LedgerMigrationError("archive", "existing archive differs from ledger.json")
        return archived
    try:
        atomic_write_bytes(archive, original, prefix="ledger-v1-")
    except OSError as exc:
        raise LedgerMigrationError("archive", str(exc)) from exc
    return original


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
