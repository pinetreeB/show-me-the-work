"""v2.6.2 goals/intent block-persistence invariant (RED-first for the 3==2 lost update).

Qwen night investigation (tmp/qwen-goals-flake-investigation.md) hypothesis H1:
`block_goals_once` / `block_intent_once` ignore a failed `save_ledger` (returns False)
and still emit a "block". The counter increment is therefore not durable, so the next
serial worker re-reads the stale counter and blocks again -> more than two blocks in one
batch (the intermittent `assert 3 == 2` on Windows CI).

These tests pin the underlying invariant deterministically by injecting a durable-write
failure, instead of relying on the non-reproducible timing race:

    A "block" decision must be backed by a persisted counter increment.

They are RED against 06371b7 (v2.6.1) and MUST turn GREEN after the fix.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core import ledger_storage
from core.gate_counters import block_goals_once, block_intent_once
from core.ledger import load_ledger, record_event


def _require_goals(root: Path) -> dict:
    _ = record_event(
        {
            "project_root": str(root),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "gate",
            "needs_goals": True,
        }
    )
    return {"project_root": str(root), "tool_name": "Edit", "file_paths": ["app.py"]}


def _require_intent(root: Path) -> dict:
    _ = record_event(
        {
            "project_root": str(root),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "gate",
            "intent_required": True,
        }
    )
    return {"project_root": str(root), "tool_name": "Edit", "file_paths": ["app.py"]}


def test_goals_block_requires_persisted_increment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _require_goals(tmp_path)
    # Inject a durable-write failure: the counter increment cannot reach disk.
    monkeypatch.setattr("core.gate_counters.save_ledger", lambda _payload, _ledger: False)

    decision = block_goals_once(payload)

    reloaded = load_ledger(payload)
    blocks = reloaded.get("goals_blocks", 0)
    # Invariant: a "block" decision must be backed by a persisted increment. Otherwise the
    # next serial worker re-reads the stale counter and blocks again (lost update -> 3==2).
    assert not (decision["decision"] == "block" and blocks == 0), (
        f"block emitted without persistence: decision={decision['decision']} goals_blocks={blocks}"
    )


def test_intent_block_requires_persisted_increment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _require_intent(tmp_path)
    monkeypatch.setattr("core.gate_counters.save_ledger", lambda _payload, _ledger: False)

    decision = block_intent_once(payload, "smtw intent record --root .")

    reloaded = load_ledger(payload)
    blocks = reloaded.get("intent_blocks", 0)
    assert not (decision["decision"] == "block" and blocks == 0), (
        f"block emitted without persistence: decision={decision['decision']} intent_blocks={blocks}"
    )


def test_replace_with_retries_recovers_from_transient_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "ledger-src.tmp"
    destination = tmp_path / "ledger.json"
    _ = source.write_text("payload", encoding="utf-8")
    real_replace = os.replace
    attempts = {"count": 0}

    def flaky_replace(src: object, dst: object) -> None:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError("transient AV lock")
        real_replace(src, dst)

    monkeypatch.setattr(ledger_storage.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ledger_storage.os, "replace", flaky_replace)

    ledger_storage._replace_with_retries(source, destination)

    assert destination.read_text(encoding="utf-8") == "payload"
    assert attempts["count"] == 3


def test_replace_with_retries_raises_after_exhausting_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "ledger-src.tmp"
    destination = tmp_path / "ledger.json"
    _ = source.write_text("payload", encoding="utf-8")
    attempts = {"count": 0}

    def always_busy(src: object, dst: object) -> None:
        attempts["count"] += 1
        raise PermissionError("permanent lock")

    monkeypatch.setattr(ledger_storage.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ledger_storage.os, "replace", always_busy)

    with pytest.raises(PermissionError):
        ledger_storage._replace_with_retries(source, destination)

    # One initial attempt plus one retry per backoff delay.
    assert attempts["count"] == len(ledger_storage.REPLACE_RETRY_DELAYS_SECONDS) + 1
