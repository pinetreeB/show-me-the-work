from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from uuid import uuid4

from .agent_log import agent_log_path as agent_log_path, append_agent_event, ledger_transaction, load_agent_events
from .ledger_migration import LedgerMigrationError as LedgerMigrationError, migrate_v1_ledger
from .ledger_schema import JsonObject as JsonObject, JsonScalar as JsonScalar, JsonValue as JsonValue, LedgerSchemaError, serialize_v2_ledger, validate_v2_ledger
from .ledger_storage import atomic_write_text, ledger_path as ledger_path, state_dir as state_dir
from .ledger_v1 import apply_v1_event, classify_change_kind as classify_change_kind, default_ledger, sequence_value
from .ledger_v2 import apply_v2_event, default_v2_ledger


def _project_root(payload: Mapping[str, JsonValue]) -> str:
    root = payload.get("project_root") or payload.get("cwd")
    return root if isinstance(root, str) and root else "."


def _agent(payload: Mapping[str, JsonValue]) -> str:
    value = payload.get("agent")
    return value if isinstance(value, str) and value else ""


def load_ledger(payload: Mapping[str, JsonValue]) -> JsonObject:
    path = ledger_path(_project_root(payload))
    try:
        loaded: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _preserve_corrupt_ledger(path)
        return default_ledger()
    except OSError:
        return default_ledger()
    if isinstance(loaded, dict):
        schema_version = loaded.get("schema_version")
        if schema_version == 2:
            return validate_v2_ledger(loaded)
        if schema_version is not None and (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != 1
        ):
            raise LedgerSchemaError("ledger.schema_version", "must be 1 or 2")
        merged = default_ledger()
        merged.update(loaded)
        return merged
    return default_ledger()


def _preserve_corrupt_ledger(path: Path) -> None:
    if not path.exists():
        return
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.corrupt-{timestamp}-{uuid4().hex}.bak")
    try:
        path.replace(backup)
    except OSError:
        return


def save_ledger(payload: Mapping[str, JsonValue], ledger: JsonObject) -> None:
    schema_version = ledger.get("schema_version")
    serialized = serialize_v2_ledger(ledger) if schema_version == 2 else json.dumps(
        ledger, ensure_ascii=False, indent=2, sort_keys=True
    )
    try:
        atomic_write_text(ledger_path(_project_root(payload)), serialized)
    except OSError:
        return


def record_event(payload: Mapping[str, JsonValue]) -> JsonObject:
    root = _project_root(payload)
    destination = ledger_path(root)
    with ledger_transaction(root):
        existed_before_load = destination.exists()
        ledger = load_ledger(payload)
        if not existed_before_load:
            ledger = default_v2_ledger()
        event_payload: dict[str, JsonValue] = dict(payload)
        _ = event_payload.pop("event_seq", None)
        event_payload["seq"] = sequence_value(ledger.get("event_seq")) + 1
        if ledger.get("schema_version") == 2:
            _ = apply_v2_event(ledger, event_payload)
        else:
            _ = apply_v1_event(ledger, event_payload)
        save_ledger(payload, ledger)
        append_agent_event(root, _agent(payload), event_payload)
        return ledger


def migrate_ledger_to_v2(payload: Mapping[str, JsonValue]) -> JsonObject:
    root = _project_root(payload)
    with ledger_transaction(root):
        return migrate_v1_ledger(root)


def load_agent_ledger(payload: Mapping[str, JsonValue]) -> JsonObject:
    agent = _agent(payload)
    if not agent:
        return load_ledger(payload)
    root = _project_root(payload)
    events = load_agent_events(root, agent)
    if events is None:
        return load_ledger(payload)
    ledger = default_ledger()
    for event in events:
        _ = apply_v1_event(ledger, event)
    return ledger
