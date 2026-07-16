from __future__ import annotations

import json
from pathlib import Path

from core.ledger import JsonObject, load_ledger, record_event
from core.verification_covers import active_turn


FIXTURE = Path(__file__).parent / "fixtures" / "v2-provenance" / "ledger.json"

HOST = "codex_cli"
AGENT = "codex"
SESSION_ID = "session"
TURN_ID = "turn-1"


def _prompt(root: str, turn_id: str = TURN_ID) -> JsonObject:
    payload: JsonObject = {
        "project_root": root,
        "event": "prompt",
        "host": HOST,
        "agent": AGENT,
        "session_id": SESSION_ID,
        "turn_id": turn_id,
        "prompt": "work",
    }
    _ = record_event(payload)
    return payload


def _observation(root: str, turn_id: str, **extra: object) -> JsonObject:
    payload: JsonObject = {
        "project_root": root,
        "event": "observation",
        "host": HOST,
        "agent": AGENT,
        "session_id": SESSION_ID,
        "turn_id": turn_id,
        **extra,
    }
    _ = record_event(payload)
    return payload


def _turn(root: str, payload: JsonObject) -> JsonObject:
    turn = active_turn(load_ledger({"project_root": root}), payload)
    assert turn is not None
    return turn


def test_valid_budget_fields_are_stored_on_the_active_turn(tmp_path: Path) -> None:
    root = str(tmp_path)
    _ = _prompt(root)

    payload = _observation(
        root,
        TURN_ID,
        provenance_budget_top_paths=[{"path": "src", "bytes": 100, "entries": 2}],
        provenance_budget_breach_path="src/big.bin",
    )

    turn = _turn(root, payload)
    assert turn["provenance_budget_top_paths"] == [{"path": "src", "bytes": 100, "entries": 2}]
    assert turn["provenance_budget_breach_path"] == "src/big.bin"


def test_subsequent_empty_budget_fields_clear_stale_values(tmp_path: Path) -> None:
    root = str(tmp_path)
    _ = _prompt(root)
    _ = _observation(
        root,
        TURN_ID,
        provenance_budget_top_paths=[{"path": "src", "bytes": 100, "entries": 2}],
        provenance_budget_breach_path="src/big.bin",
    )

    payload = _observation(
        root,
        TURN_ID,
        provenance_budget_top_paths=[],
        provenance_budget_breach_path=None,
    )

    turn = _turn(root, payload)
    assert "provenance_budget_top_paths" not in turn
    assert "provenance_budget_breach_path" not in turn


def test_invalid_budget_values_are_normalized_and_cleared(tmp_path: Path) -> None:
    root = str(tmp_path)
    _ = _prompt(root)
    _ = _observation(
        root,
        TURN_ID,
        provenance_budget_top_paths=[{"path": "src", "bytes": 100, "entries": 2}],
        provenance_budget_breach_path="src/big.bin",
    )

    payload = _observation(
        root,
        TURN_ID,
        provenance_budget_top_paths=[{"path": "", "bytes": -1, "entries": "not-an-int"}],
        provenance_budget_breach_path=123,
    )

    turn = _turn(root, payload)
    assert "provenance_budget_top_paths" not in turn
    assert "provenance_budget_breach_path" not in turn


def test_missing_budget_keys_preserve_existing_state(tmp_path: Path) -> None:
    # Given: a turn that already carries budget diagnostics from a prior observation.
    root = str(tmp_path)
    _ = _prompt(root)
    _ = _observation(
        root,
        TURN_ID,
        provenance_budget_top_paths=[{"path": "src", "bytes": 100, "entries": 2}],
        provenance_budget_breach_path="src/big.bin",
    )

    # When: an older-shaped event with no budget keys at all is applied (backward compat).
    payload: JsonObject = {
        "project_root": root,
        "event": "invocation",
        "host": HOST,
        "agent": AGENT,
        "session_id": SESSION_ID,
        "turn_id": TURN_ID,
        "invocation_id": "inv-1",
    }
    _ = record_event(payload)

    # Then: the previously stored diagnostics remain untouched.
    turn = _turn(root, payload)
    assert turn["provenance_budget_top_paths"] == [{"path": "src", "bytes": 100, "entries": 2}]
    assert turn["provenance_budget_breach_path"] == "src/big.bin"


def test_legacy_v2_ledger_fixture_without_new_fields_loads_and_accepts_new_events(
    tmp_path: Path,
) -> None:
    # Given: a pre-existing v2 ledger fixture that predates the budget diagnostic fields.
    destination = tmp_path / ".fable-lite" / "ledger.json"
    destination.parent.mkdir()
    destination.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    root = str(tmp_path)

    # When: it is loaded (must not raise) ...
    legacy_ledger = load_ledger({"project_root": root})
    assert "provenance_budget_top_paths" not in json.dumps(legacy_ledger)
    original_default_turn = legacy_ledger["active_turns"]["default"]

    # ... and a fresh turn records an observation carrying the new budget fields.
    _ = _prompt(root)
    payload = _observation(
        root,
        TURN_ID,
        provenance_budget_top_paths=[{"path": "legacy", "bytes": 1, "entries": 1}],
        provenance_budget_breach_path="legacy/hit.bin",
    )

    # Then: the new turn stores the diagnostics and the old "default" turn is untouched.
    reloaded = load_ledger({"project_root": root})
    new_turn = _turn(root, payload)
    assert new_turn["provenance_budget_top_paths"] == [{"path": "legacy", "bytes": 1, "entries": 1}]
    assert new_turn["provenance_budget_breach_path"] == "legacy/hit.bin"
    assert reloaded["active_turns"]["default"] == original_default_turn


def test_new_budget_fields_round_trip_through_json_serialization(tmp_path: Path) -> None:
    root = str(tmp_path)
    _ = _prompt(root)
    payload = _observation(
        root,
        TURN_ID,
        provenance_budget_top_paths=[
            {"path": "src", "bytes": 200, "entries": 3},
            {"path": "assets", "bytes": 50, "entries": 1},
        ],
        provenance_budget_breach_path="src/oversized.bin",
    )

    raw = (tmp_path / ".fable-lite" / "ledger.json").read_text(encoding="utf-8")
    reparsed = json.loads(raw)
    turn = active_turn(reparsed, payload)

    assert turn is not None
    assert turn["provenance_budget_top_paths"] == [
        {"path": "src", "bytes": 200, "entries": 3},
        {"path": "assets", "bytes": 50, "entries": 1},
    ]
    assert turn["provenance_budget_breach_path"] == "src/oversized.bin"
