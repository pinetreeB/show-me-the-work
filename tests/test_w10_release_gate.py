from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import core.release_gate as release_gate_module
from core.ledger import record_event
from core.ledger_schema import JsonValue
from core.release_gate import DEFAULT_RECEIPTS_DIR, auto_migration_enabled

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "v2-provenance"
AUTO_MIGRATION_ENV = "FABLE_LITE_AUTO_MIGRATION"


def _receipt(path: Path, payload: dict[str, JsonValue]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _legacy_ledger(root: Path) -> Path:
    state = root / ".fable-lite"
    state.mkdir()
    ledger = state / "ledger.json"
    ledger.write_text((FIXTURE_DIR / "v1-ledger.json").read_text(encoding="utf-8"), encoding="utf-8")
    return ledger


def test_packaged_release_receipts_still_require_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DEFAULT_RECEIPTS_DIR.parent == Path(
        release_gate_module.__file__
    ).resolve().parent
    assert DEFAULT_RECEIPTS_DIR.name == "release_receipts"
    monkeypatch.delenv(AUTO_MIGRATION_ENV, raising=False)
    assert auto_migration_enabled() is False
    monkeypatch.setenv(AUTO_MIGRATION_ENV, "0")
    assert auto_migration_enabled() is False
    monkeypatch.setenv(AUTO_MIGRATION_ENV, "true")
    assert auto_migration_enabled() is False
    monkeypatch.setenv(AUTO_MIGRATION_ENV, "1")
    assert auto_migration_enabled() is True


def test_auto_migration_gate_requires_two_green_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the W9 receipt is green but the W10 receipt is missing or red.
    monkeypatch.setenv(AUTO_MIGRATION_ENV, "1")
    _receipt(
        tmp_path / "provenance-latest.json",
        {
            "hard_gate": {"passed": True},
            "golden": {"cases": 200, "false_negatives": 0, "false_positives": 0},
            "canonical_replay": {"failures": 0},
            "git_non_git": {"mismatches": []},
        },
    )

    # When: the release guard evaluates each receipt state.
    assert auto_migration_enabled(tmp_path) is False
    _receipt(tmp_path / "bench-latest.json", {"hard_gate": {"passed": False}, "slo": {"passed": False}})
    assert auto_migration_enabled(tmp_path) is False
    _receipt(tmp_path / "bench-latest.json", {"hard_gate": {"passed": True}, "slo": {"passed": True}})
    assert auto_migration_enabled(tmp_path) is False
    _receipt(
        tmp_path / "bench-latest.json",
        {
            "hard_gate": {"passed": True},
            "slo": {"passed": True, "scales": {"1k": {"passed": True}, "10k": {"passed": True}}},
        },
    )

    # Then: only two green receipts unlock the release migration path.
    assert auto_migration_enabled(tmp_path) is True


def test_record_event_migrates_legacy_only_when_release_guard_is_green(tmp_path: Path) -> None:
    # Given: an isolated legacy ledger.
    ledger = _legacy_ledger(tmp_path)
    payload = {"project_root": str(tmp_path), "event": "scope_warning", "message": "release"}

    # When: the release receipt guard is green.
    with patch("core.ledger.auto_migration_enabled", return_value=True):
        migrated = record_event(payload)

    # Then: the ordinary event path uses the one-shot migration engine and preserves its archive.
    assert migrated["schema_version"] == 2
    assert ledger.with_name("ledger.v1.json.bak").exists()


def test_record_event_keeps_legacy_when_release_guard_is_red(tmp_path: Path) -> None:
    # Given: an isolated legacy ledger and a red release guard.
    ledger = _legacy_ledger(tmp_path)

    # When: the normal event path runs before release evidence is available.
    with patch("core.ledger.auto_migration_enabled", return_value=False):
        legacy = record_event({"project_root": str(tmp_path), "event": "scope_warning", "message": "hold"})

    # Then: v1 dual-read semantics remain available and no archive is created.
    assert "schema_version" not in legacy
    assert not ledger.with_name("ledger.v1.json.bak").exists()
