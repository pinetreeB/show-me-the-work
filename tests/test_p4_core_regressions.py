from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import core.agent_log as agent_log
from core.ledger import JsonObject, load_ledger, record_event
from core.verify_state import evaluate_stop
from fable_lite.check_support import has_successful_verification


def _record_prompt(root: Path) -> None:
    record_event(
        {
            "project_root": str(root),
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "app.py 수정",
        }
    )


def _record_change(root: Path, path: str, kind: str) -> None:
    record_event(
        {
            "project_root": str(root),
            "event": "change",
            "path": path,
            "kind": kind,
        }
    )


def _record_success(root: Path) -> None:
    record_event(
        {
            "project_root": str(root),
            "event": "verification",
            "command": "python -m pytest tests/",
            "success": True,
            "evidence": "1 passed",
        }
    )


def _load(root: Path) -> JsonObject:
    return load_ledger({"project_root": str(root)})


def test_check_support_rejects_success_that_predates_latest_change(tmp_path: Path) -> None:
    # Given: successful verification is followed by a newer code change.
    _record_prompt(tmp_path)
    _record_change(tmp_path, "app.py", "code")
    _record_success(tmp_path)
    _record_change(tmp_path, "app.py", "code")

    # When: both completion surfaces inspect the same ledger.
    ledger = _load(tmp_path)
    stop = evaluate_stop({"project_root": str(tmp_path)})

    # Then: check and Stop both reject the stale success.
    assert has_successful_verification(ledger) is False
    assert stop["decision"] == "block"


def test_docs_change_after_fresh_code_verification_does_not_advance_epoch(tmp_path: Path) -> None:
    # Given: code is changed and verified before a final documentation edit.
    _record_prompt(tmp_path)
    _record_change(tmp_path, "app.py", "code")
    code_change_seq = _load(tmp_path)["last_change_seq"]
    verified = record_event(
        {
            "project_root": str(tmp_path),
            "event": "verification",
            "command": "python -m pytest tests/",
            "success": True,
            "evidence": "1 passed",
        }
    )
    verification_results = verified["verification_results"]
    assert isinstance(verification_results, list) and verification_results
    verification = verification_results[0]
    assert isinstance(verification, dict)
    verification_seq = verification["seq"]
    assert isinstance(verification_seq, int) and not isinstance(verification_seq, bool)
    _record_change(tmp_path, "README.md", "docs")

    # When: the final docs edit is evaluated.
    ledger = _load(tmp_path)
    result = evaluate_stop({"project_root": str(tmp_path)})

    # Then: docs does not invalidate the fresh code verification.
    assert ledger["last_change_seq"] == code_change_seq
    assert result["decision"] == "allow"


def test_docs_change_cannot_hide_earlier_unverified_code_change(tmp_path: Path) -> None:
    # Given: an unverified code change is followed by a docs change.
    _record_prompt(tmp_path)
    _record_change(tmp_path, "app.py", "code")
    code_change_seq = _load(tmp_path)["last_change_seq"]
    _record_change(tmp_path, "README.md", "docs")

    # When: completion is evaluated without verification.
    ledger = _load(tmp_path)
    result = evaluate_stop({"project_root": str(tmp_path)})

    # Then: cumulative code state remains verification-required and blocked.
    assert ledger["last_change_seq"] == code_change_seq
    assert result["decision"] == "block"


def test_stale_recovery_does_not_steal_lock_from_live_pid(tmp_path: Path) -> None:
    # Given: an old-looking lock still names a live process owner.
    state = tmp_path / ".fable-lite"
    state.mkdir()
    lock = state / "ledger.lock"
    lock.write_text(f"{os.getpid()}:live-owner", encoding="ascii")
    os.utime(lock, (0, 0))

    # When/Then: stale age alone cannot steal a live owner's lock.
    timed_out = False
    with (
        patch.object(agent_log, "LOCK_WAIT_SECONDS", 0.03),
        patch.object(agent_log, "STALE_LOCK_SECONDS", 0.0),
    ):
        try:
            with agent_log.ledger_transaction(str(tmp_path)):
                pass
        except TimeoutError:
            timed_out = True
    assert timed_out is True


def test_transaction_release_preserves_replaced_owner_lock(tmp_path: Path) -> None:
    # Given: another owner token replaces the lock metadata before release.
    lock = tmp_path / ".fable-lite" / "ledger.lock"
    with agent_log.ledger_transaction(str(tmp_path)):
        lock.write_text("999999:successor-owner", encoding="ascii")

    # When: the original transaction finishes, release has already run.
    owner = lock.read_text(encoding="ascii")

    # Then: the former owner does not unlink its successor's lock.
    assert owner == "999999:successor-owner"
