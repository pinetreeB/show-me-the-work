from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import core.ledger as ledger_module
import core.ledger_migration as migration_module
from adapters.codex_cli import common as codex_common
from adapters.codex_cli import pre_tool_use as codex_pre_tool_use
from core.agent_log import ledger_transaction
from core.ledger import load_ledger, save_ledger
from core.ledger_schema import JsonObject
from core.ledger_storage import ledger_path
from core.ledger_v2 import apply_v2_event, default_v2_ledger


ROOT = Path(__file__).resolve().parents[1]


def _seed_ledger(root: Path, prompt: str) -> None:
    ledger = default_v2_ledger()
    ledger["prompt"] = prompt
    assert save_ledger({"project_root": str(root)}, ledger)


def _statusless_ledger() -> JsonObject:
    ledger = default_v2_ledger()
    _ = apply_v2_event(
        ledger,
        {
            "event": "prompt",
            "seq": 1,
            "host": "codex_cli",
            "session_id": "cache-test",
            "agent": "codex",
            "turn_id": "cache-turn",
            "prompt": "cache test",
        },
    )
    turns = ledger["active_turns"]
    assert isinstance(turns, dict)
    turn = turns["codex_cli:cache-test:codex"]
    assert isinstance(turn, dict)
    turn["invocations"] = {
        "tool:legacy": {
            "candidate_paths": ["core/ledger.py"],
            "started_seq": 1,
            "started_at": "2020-01-01T00:00:00+00:00",
        }
    }
    return ledger


def test_process_cache_parses_unchanged_statusless_ledger_once(
    tmp_path: Path,
) -> None:
    # Given
    destination = ledger_path(str(tmp_path))
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps(_statusless_ledger()), encoding="utf-8")
    real_loads = json.loads

    # When
    with patch.object(ledger_module.json, "loads", wraps=real_loads) as loads:
        with ledger_module.ledger_read_cache():
            first = load_ledger({"project_root": str(tmp_path)})
            second = load_ledger({"project_root": str(tmp_path)})

    # Then
    assert loads.call_count == 1
    assert second is first
    assert first["attribution_degraded"] is True


def test_real_codex_pretool_adapter_reads_statusless_ledger_once(
    tmp_path: Path,
) -> None:
    # Given
    _ = (tmp_path / ".git").mkdir()
    _ = (tmp_path / ".smtw.toml").write_text(
        "schema_version = 1\nsupervision = true\n",
        encoding="utf-8",
    )
    destination = ledger_path(str(tmp_path))
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps(_statusless_ledger()), encoding="utf-8")
    payload: JsonObject = {
        "cwd": str(tmp_path),
        "project_root": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "git status --short"},
        "session_id": "cache-test",
        "turn_id": "cache-turn",
        "tool_use_id": "tool:cache-test",
        "agent": "codex",
    }
    real_uncached_load = ledger_module._load_ledger_uncached

    # When
    with (
        patch.object(codex_common, "read_payload", return_value=payload),
        patch.object(codex_common, "emit", return_value=0),
        patch.object(
            ledger_module,
            "_load_ledger_uncached",
            wraps=real_uncached_load,
        ) as uncached_load,
    ):
        result = codex_pre_tool_use.main()

    # Then
    assert result == 0
    assert uncached_load.call_count == 1


def test_process_cache_reloads_inside_lock_when_fingerprint_changes(
    tmp_path: Path,
) -> None:
    # Given
    _seed_ledger(tmp_path, "old")
    destination = ledger_path(str(tmp_path))
    # When
    with ledger_module.ledger_read_cache():
        assert load_ledger({"project_root": str(tmp_path)})["prompt"] == "old"
        replacement = default_v2_ledger()
        replacement["prompt"] = "new-value-from-peer"
        replacement_text = ledger_module.serialize_v2_ledger(replacement)
        destination.write_text(replacement_text, encoding="utf-8")
        with ledger_transaction(str(tmp_path)):
            reloaded = load_ledger({"project_root": str(tmp_path)})

    # Then
    assert reloaded["prompt"] == "new-value-from-peer"


def test_successful_save_invalidates_process_cache(tmp_path: Path) -> None:
    # Given
    _seed_ledger(tmp_path, "before")

    # When
    with ledger_module.ledger_read_cache():
        cached = load_ledger({"project_root": str(tmp_path)})
        replacement = default_v2_ledger()
        replacement["prompt"] = "after"
        assert save_ledger({"project_root": str(tmp_path)}, replacement)
        reloaded = load_ledger({"project_root": str(tmp_path)})

    # Then
    assert reloaded["prompt"] == "after"
    assert reloaded is not cached


def test_process_cache_rejects_stale_read_after_external_process_write(
    tmp_path: Path,
) -> None:
    # Given
    _seed_ledger(tmp_path, "before")
    code = (
        "import sys\n"
        "from core.ledger import save_ledger\n"
        "from core.ledger_v2 import default_v2_ledger\n"
        "ledger = default_v2_ledger()\n"
        "ledger['prompt'] = 'written-by-peer-process'\n"
        "raise SystemExit(0 if save_ledger({'project_root': sys.argv[1]}, ledger) else 1)\n"
    )

    # When
    with ledger_module.ledger_read_cache():
        assert load_ledger({"project_root": str(tmp_path)})["prompt"] == "before"
        result = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        reloaded = load_ledger({"project_root": str(tmp_path)})

    # Then
    assert result.returncode == 0, result.stderr
    assert reloaded["prompt"] == "written-by-peer-process"


def test_backfill_returns_original_when_no_status_is_missing() -> None:
    # Given
    ledger = default_v2_ledger()

    # When
    migrated, changed = migration_module.backfill_invocation_statuses(ledger)

    # Then
    assert changed == 0
    assert migrated is ledger


def test_backfill_copies_only_branches_containing_statusless_rows() -> None:
    # Given
    observed_at = datetime(2026, 8, 3, tzinfo=UTC)
    untouched: JsonObject = {"event": "preserve-identity"}
    entry: JsonObject = {
        "candidate_paths": ["core/ledger.py"],
        "started_seq": 1,
        "started_at": "2020-01-01T00:00:00+00:00",
    }
    turn: JsonObject = {
        "invocations": {"tool:legacy": entry},
        "unrelated": untouched,
    }
    ledger: JsonObject = {
        "schema_version": 2,
        "active_turns": {"codex_cli:cache-test:codex": turn},
        "unrelated": untouched,
    }

    # When
    migrated, changed = migration_module.backfill_invocation_statuses(
        ledger,
        observed_at=observed_at,
    )

    # Then
    migrated_turns = migrated["active_turns"]
    assert isinstance(migrated_turns, dict)
    migrated_turn = migrated_turns["codex_cli:cache-test:codex"]
    assert isinstance(migrated_turn, dict)
    migrated_invocations = migrated_turn["invocations"]
    assert isinstance(migrated_invocations, dict)
    migrated_entry = migrated_invocations["tool:legacy"]
    assert isinstance(migrated_entry, dict)
    assert changed == 1
    assert migrated is not ledger
    assert migrated["unrelated"] is untouched
    assert migrated_turn["unrelated"] is untouched
    assert migrated_entry["status"] == "closed"
    assert "status" not in entry
