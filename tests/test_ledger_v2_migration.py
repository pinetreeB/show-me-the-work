from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import core.ledger as ledger_module
from core.agent_log import agent_log_path, load_agent_events
from core.ledger import JsonObject, JsonValue, record_event
from core.verify_state import has_successful_verification


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "v2-provenance"
OS_REPLACE = os.replace


def _fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _json_fixture(name: str) -> JsonObject:
    value: JsonValue = json.loads(_fixture(name))
    assert isinstance(value, dict)
    return value


def _write_ledger(root: Path, serialized: str) -> Path:
    directory = root / ".fable-lite"
    directory.mkdir()
    destination = directory / "ledger.json"
    destination.write_text(serialized, encoding="utf-8")
    return destination


def _turn(agent: str, turn_id: str, prompt: str) -> JsonObject:
    return {
        "turn_id": turn_id,
        "start_seq": 1,
        "baseline_snapshot_id": "blake2b-256:before",
        "current_snapshot_id": "blake2b-256:after",
        "pending_change_ids": [],
        "blocks": {"stop": 0},
        "agent": agent,
        "task_mode": "deep",
        "prompt": prompt,
        "packs": [],
        "changed_files_seen": [],
        "change_kinds": [],
        "verification_commands": [],
        "verification_results": [],
        "event_seq": 1,
        "last_change_seq": 0,
        "goals_blocks": 0,
        "intent_blocks": 0,
        "requires_investigation_compliance": False,
        "needs_goals": False,
        "intent_required": False,
        "ambiguity_score": 0,
        "scope_warnings": [],
    }


def test_migration_preserves_v1_seq_fixture_archive_and_projection(tmp_path: Path) -> None:
    # Given: a sequenced v1 ledger fixture with no pre-existing archive.
    original = _fixture("v1-ledger.json")
    destination = _write_ledger(tmp_path, original)

    # When: the explicit fixture-only migration entry point is invoked.
    migrated = ledger_module.migrate_ledger_to_v2({"project_root": str(tmp_path)})

    # Then: v2 keeps the legacy projection and an immutable byte-for-byte archive.
    archive = destination.with_name("ledger.v1.json.bak")
    turn = migrated["active_turns"]
    assert migrated["schema_version"] == 2
    assert archive.read_text(encoding="utf-8") == original
    assert isinstance(turn, dict)
    default_turn = turn["default"]
    assert isinstance(default_turn, dict)
    assert default_turn["migration_mode"] == "legacy_turn"
    assert migrated["event_seq"] == 3
    assert migrated["last_change_seq"] == 2
    assert migrated["changed_files_seen"] == default_turn["changed_files_seen"]
    assert migrated["verification_results"] == default_turn["verification_results"]
    default_blocks = default_turn["blocks"]
    assert isinstance(default_blocks, dict)
    assert migrated["stop_blocks"] == default_blocks["stop"]


def test_seq_less_legacy_success_expires_before_the_first_new_v2_change(tmp_path: Path) -> None:
    # Given: a v1 ledger whose verification has no sequence despite old change metadata.
    legacy = _json_fixture("v1-ledger.json")
    results = legacy["verification_results"]
    assert isinstance(results, list)
    _ = results[0].pop("seq", None) if isinstance(results[0], dict) else None
    _ = _write_ledger(tmp_path, json.dumps(legacy, ensure_ascii=False))
    migrated = ledger_module.migrate_ledger_to_v2({"project_root": str(tmp_path)})

    # When: the legacy turn is replaced and a new v2 change is recorded.
    assert has_successful_verification(migrated) is True
    _ = record_event(
        {"project_root": str(tmp_path), "event": "prompt", "prompt": "new v2 turn"}
    )
    changed = record_event(
        {"project_root": str(tmp_path), "event": "change", "path": "new.py"}
    )

    # Then: unsequenced legacy evidence never verifies the new v2 change.
    assert has_successful_verification(changed) is False


def test_migration_restores_v1_bytes_after_the_final_atomic_replace_fails(tmp_path: Path) -> None:
    # Given: a v1 ledger and a one-time failure at the v2 destination replacement.
    original = _fixture("v1-ledger.json")
    destination = _write_ledger(tmp_path, original)
    failed = False

    def fail_once(source: str, target: str | Path) -> None:
        nonlocal failed
        if Path(target) == destination and not failed:
            failed = True
            raise OSError("injected migration replace failure")
        OS_REPLACE(source, target)

    # When: migration hits the injected write fault.
    failure: ledger_module.LedgerMigrationError | None = None
    with patch("core.ledger_storage.os.replace", side_effect=fail_once):
        try:
            _ = ledger_module.migrate_ledger_to_v2({"project_root": str(tmp_path)})
        except ledger_module.LedgerMigrationError as exc:
            failure = exc
        else:
            raise AssertionError("expected migration write failure")

    # Then: archive-backed automatic restoration leaves the original v1 bytes in place.
    assert failure is not None
    assert failure.stage == "write"
    assert failed is True
    assert destination.read_text(encoding="utf-8") == original
    assert destination.with_name("ledger.v1.json.bak").read_text(encoding="utf-8") == original


def test_v2_prompt_replaces_only_its_own_turn_and_refreshes_projection(tmp_path: Path) -> None:
    # Given: a v2 ledger owned by two independently active agents.
    ledger = _json_fixture("ledger.json")
    active = ledger["active_turns"]
    assert isinstance(active, dict)
    active.clear()
    active["host:one:alpha"] = _turn("alpha", "old-alpha", "old alpha")
    active["host:two:beta"] = _turn("beta", "beta-turn", "beta prompt")
    _ = _write_ledger(tmp_path, json.dumps(ledger, ensure_ascii=False))

    # When: alpha starts a replacement prompt turn.
    result = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "agent": "alpha",
            "host": "host",
            "session_id": "one",
            "turn_id": "alpha-turn",
            "prompt": "new alpha",
            "baseline_snapshot_id": "blake2b-256:baseline",
            "current_snapshot_id": "blake2b-256:current",
        }
    )

    # Then: beta is untouched while the v1 projection derives from alpha's new turn.
    turns = result["active_turns"]
    assert isinstance(turns, dict)
    assert turns["host:two:beta"] == _turn("beta", "beta-turn", "beta prompt")
    alpha = turns["host:one:alpha"]
    assert isinstance(alpha, dict)
    assert alpha["turn_id"] == "alpha-turn"
    assert alpha["agent"] == "alpha"
    assert result["agent"] == "alpha"
    assert result["prompt"] == "new alpha"
    assert result["changed_files_seen"] == alpha["changed_files_seen"]
    alpha_blocks = alpha["blocks"]
    assert isinstance(alpha_blocks, dict)
    assert result["stop_blocks"] == alpha_blocks["stop"]


def test_migration_is_idempotent_and_never_rewrites_the_archive(tmp_path: Path) -> None:
    # Given: an untouched v1 ledger fixture.
    original = _fixture("v1-ledger.json")
    destination = _write_ledger(tmp_path, original)

    # When: the opt-in migration is requested twice.
    first = ledger_module.migrate_ledger_to_v2({"project_root": str(tmp_path)})
    first_bytes = destination.read_bytes()
    second = ledger_module.migrate_ledger_to_v2({"project_root": str(tmp_path)})

    # Then: the existing v2 state and immutable v1 archive are unchanged.
    assert second == first
    assert destination.read_bytes() == first_bytes
    assert destination.with_name("ledger.v1.json.bak").read_text(encoding="utf-8") == original


def test_v1_record_event_does_not_auto_migrate_an_existing_ledger(tmp_path: Path) -> None:
    # Given: a real legacy ledger that has not opted into migration.
    original = _fixture("v1-ledger.json")
    destination = _write_ledger(tmp_path, original)

    # When: the normal record path receives its next v1-compatible event.
    with patch("core.ledger.auto_migration_enabled", return_value=False):
        result = record_event({"project_root": str(tmp_path), "event": "scope_warning", "message": "legacy"})

    # Then: the v1 format remains active and no migration archive is created.
    persisted = json.loads(destination.read_text(encoding="utf-8"))
    assert "schema_version" not in persisted
    assert "schema_version" not in result
    assert not destination.with_name("ledger.v1.json.bak").exists()


def test_agent_jsonl_dual_reader_marks_legacy_entries_without_rewriting(tmp_path: Path) -> None:
    # Given: one old JSONL event and one complete v2 audit event.
    path = agent_log_path(str(tmp_path), "codex")
    path.parent.mkdir(parents=True)
    original = '{"event":"change","path":"legacy.py"}\n' + json.dumps(
        _json_fixture("change-event.json"), ensure_ascii=False
    )
    path.write_text(original + "\n", encoding="utf-8")

    # When: the append-only log is read through the normalization boundary.
    events = load_agent_events(str(tmp_path), "codex")

    # Then: legacy is labeled, v2 remains available, and source bytes are never rewritten.
    assert events is not None
    assert events[0]["legacy_event"] is True
    assert events[0]["path"] == "legacy.py"
    assert events[1]["schema_version"] == 2
    assert path.read_text(encoding="utf-8") == original + "\n"
