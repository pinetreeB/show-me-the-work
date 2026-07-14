from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from unittest.mock import patch

import core.ledger as ledger_module
from core.ledger import JsonObject, agent_log_path, load_ledger, record_event
from core.verify_state import evaluate_stop


def _record_prompt(root: Path, mode: str = "deep") -> JsonObject:
    return record_event(
        {
            "project_root": str(root),
            "event": "prompt",
            "task_mode": mode,
            "prompt": "app.py 수정",
        }
    )


def _record_change(root: Path, path: str = "app.py", kind: str = "code") -> JsonObject:
    return record_event(
        {
            "project_root": str(root),
            "event": "change",
            "path": path,
            "kind": kind,
        }
    )


def _record_verification(root: Path) -> JsonObject:
    return record_event(
        {
            "project_root": str(root),
            "event": "verification",
            "command": "python -m pytest tests/",
            "success": True,
            "evidence": "1 passed",
        }
    )


def _int_field(ledger: JsonObject, key: str) -> int:
    value = ledger[key]
    assert isinstance(value, int)
    return value


def _write_ledger(root: Path, ledger: JsonObject) -> None:
    state_dir = root / ".fable-lite"
    state_dir.mkdir()
    _ = (state_dir / "ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False),
        encoding="utf-8",
    )


def _legacy_ledger() -> JsonObject:
    return {
        "task_mode": "deep",
        "changed_files_seen": ["legacy.py"],
        "change_kinds": ["code"],
        "verification_results": [
            {
                "command": "python -m pytest tests/",
                "success": True,
                "evidence": "1 passed",
            }
        ],
    }


def test_record_event_assigns_strictly_increasing_verification_epoch_sequences(
    tmp_path: Path,
) -> None:
    # Given: a new turn with repeated edits to the same file.
    states = [
        _record_prompt(tmp_path),
        _record_change(tmp_path),
        _record_verification(tmp_path),
        _record_change(tmp_path),
        _record_verification(tmp_path),
    ]

    # When: the aggregate ledger and verification evidence are inspected.
    event_seqs = [_int_field(state, "event_seq") for state in states]
    final_ledger = states[-1]
    results = final_ledger["verification_results"]
    assert isinstance(results, list)
    result_seqs = [_int_field(result, "seq") for result in results if isinstance(result, dict)]

    # Then: every event advances once and each verification retains its epoch.
    assert event_seqs == sorted(set(event_seqs))
    assert len(event_seqs) == len(set(event_seqs))
    assert _int_field(final_ledger, "last_change_seq") == event_seqs[-2]
    assert result_seqs == [event_seqs[2], event_seqs[4]]


def test_record_event_owns_seq_and_agent_log_persists_it(tmp_path: Path) -> None:
    # Given: a caller attempts to supply its own event sequence.
    ledger = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "app.py 수정",
            "agent": "codex",
            "seq": 999,
        }
    )

    # When: the persisted agent event is read back.
    path = agent_log_path(str(tmp_path), "codex")
    event = json.loads(path.read_text(encoding="utf-8"))

    # Then: record_event assigned one authoritative sequence everywhere.
    assert ledger["event_seq"] == 1
    assert isinstance(event, dict)
    assert event["seq"] == 1


def test_record_event_serializes_concurrent_updates(tmp_path: Path) -> None:
    # Given: several hooks read the same ledger before any delayed write can finish.
    _record_prompt(tmp_path)
    paths = [f"module_{index}.py" for index in range(8)]
    original_load = ledger_module.load_ledger

    def delayed_load(payload: dict[str, object]) -> JsonObject:
        ledger = original_load(payload)
        time.sleep(0.1)
        return ledger

    # When: all hooks record unique changes concurrently.
    with patch.object(ledger_module, "load_ledger", side_effect=delayed_load):
        with ThreadPoolExecutor(max_workers=len(paths)) as pool:
            states = list(pool.map(lambda path: _record_change(tmp_path, path), paths))

    # Then: the serialized transaction preserves every change and sequence.
    ledger = load_ledger({"project_root": str(tmp_path)})
    assert ledger["event_seq"] == len(paths) + 1
    changed = ledger["changed_files_seen"]
    assert isinstance(changed, list)
    assert {item for item in changed if isinstance(item, str)} == set(paths)
    assert sorted(_int_field(state, "event_seq") for state in states) == list(range(2, len(paths) + 2))


def test_stop_gate_requires_successful_verification_after_latest_change(
    tmp_path: Path,
) -> None:
    cases = (("verify-edit-stale", True, "block"), ("edit-verify-fresh", False, "allow"))
    for case_name, verification_first, expected_decision in cases:
        # Given: an isolated deep task with a known change/verification order.
        case_root = tmp_path / case_name
        case_root.mkdir()
        _record_prompt(case_root)
        if verification_first:
            _record_verification(case_root)
            _record_change(case_root)
        else:
            _record_change(case_root)
            _record_verification(case_root)

        # When: completion is evaluated.
        result = evaluate_stop({"project_root": str(case_root)})

        # Then: only verification after the latest change permits completion.
        assert result["decision"] == expected_decision, case_name


def test_stop_gate_rejects_verification_at_same_sequence_as_latest_change(tmp_path: Path) -> None:
    # Given: a sequenced ledger whose successful verification ties the latest change.
    _write_ledger(
        tmp_path,
        {
            "task_mode": "deep",
            "changed_files_seen": ["app.py"],
            "change_kinds": ["code"],
            "event_seq": 2,
            "last_change_seq": 2,
            "verification_results": [
                {"command": "pytest", "success": True, "evidence": "1 passed", "seq": 2}
            ],
        },
    )

    # When: completion is evaluated at the equality boundary.
    result = evaluate_stop({"project_root": str(tmp_path)})

    # Then: verification must be strictly newer than the change.
    assert result["decision"] == "block"


def test_v1_seq_less_ledger_preserves_legacy_stop_decision(tmp_path: Path) -> None:
    # Given: an untouched v1 ledger with one successful verification and no seq fields.
    legacy = _legacy_ledger()
    _write_ledger(tmp_path, legacy)

    # When: the old ledger is loaded and completion is evaluated.
    loaded = load_ledger({"project_root": str(tmp_path)})
    result = evaluate_stop({"project_root": str(tmp_path)})

    # Then: it loads without migration failure and preserves the legacy any-success decision.
    assert loaded["verification_results"] == legacy["verification_results"]
    assert result["decision"] == "allow"


def test_first_new_change_invalidates_v1_seq_less_verification(tmp_path: Path) -> None:
    # Given: an existing v1 ledger whose success predates the upgrade.
    _write_ledger(tmp_path, _legacy_ledger())

    # When: record_event assigns a sequence to the first post-upgrade change.
    with patch("core.ledger.auto_migration_enabled", return_value=False):
        _record_change(tmp_path, "new.py")
    result = evaluate_stop({"project_root": str(tmp_path)})

    # Then: the old unsequenced success cannot verify the new change.
    assert result["decision"] == "block"


def test_quick_mode_requires_verification_for_non_document_changes(
    tmp_path: Path,
) -> None:
    # Given: a quick turn made a code change without running verification.
    _record_prompt(tmp_path, "quick")
    _record_change(tmp_path, "app.py", "code")

    # When: completion is evaluated.
    result = evaluate_stop({"project_root": str(tmp_path)})

    # Then: task mode cannot exempt a non-document change from verification.
    assert result["decision"] == "block"


def test_quick_mode_keeps_no_change_docs_only_and_fresh_verification_paths(
    tmp_path: Path,
) -> None:
    cases = (
        ("no-change", None, None, False, "allow"),
        ("docs-only", "README.md", "docs", False, "allow"),
        ("verified-code", "app.py", "code", True, "allow"),
    )
    for case_name, path, kind, verified, expected in cases:
        case_root = tmp_path / case_name
        case_root.mkdir()
        _record_prompt(case_root, "quick")
        if path is not None and kind is not None:
            _record_change(case_root, path, kind)
        if verified:
            _record_verification(case_root)

        result = evaluate_stop({"project_root": str(case_root)})

        assert result["decision"] == expected, case_name


def test_scope_too_large_is_advisory_unless_known_changes_need_verification(
    tmp_path: Path,
) -> None:
    cases = (("no-known-change", False, "allow"), ("known-change", True, "block"))
    for case_name, changed, expected in cases:
        case_root = tmp_path / case_name
        case_root.mkdir()
        _ = record_event(
            {
                "project_root": str(case_root),
                "event": "prompt",
                "task_mode": "quick",
                "prompt": "work",
                "provenance_status": "scope_too_large",
                "provenance_status_reason": "entry_limit",
                "provenance_incomplete": False,
            }
        )
        if changed:
            _record_change(case_root)

        result = evaluate_stop({"project_root": str(case_root)})

        assert result["decision"] == expected
        if not changed:
            assert "scope too large" in str(result["message"])


def test_scope_too_large_blocks_local_or_unknown_mutation_capability(
    tmp_path: Path,
) -> None:
    cases = (
        ("proven-read-only", False, "allow"),
        ("local-or-unknown", True, "block"),
    )
    for case_name, mutation_capable, expected in cases:
        case_root = tmp_path / case_name
        case_root.mkdir()
        _ = record_event(
            {
                "project_root": str(case_root),
                "event": "prompt",
                "task_mode": "quick",
                "prompt": "work",
                "provenance_status": "scope_too_large",
                "provenance_status_reason": "entry_limit",
                "provenance_incomplete": False,
            }
        )
        if mutation_capable:
            _ = record_event(
                {
                    "project_root": str(case_root),
                    "event": "observation",
                    "provenance_status": "scope_too_large",
                    "provenance_status_reason": "entry_limit",
                    "provenance_incomplete": False,
                    "provenance_mutation_capable": True,
                }
            )

        result = evaluate_stop({"project_root": str(case_root)})

        assert result["decision"] == expected, case_name
