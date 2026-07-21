from __future__ import annotations

import os
from pathlib import Path

import pytest

import core.agent_log as agent_log
import core.file_lock as file_lock
from core.state_layout import MIGRATION_LOCK_NAME, migration_lock_path


def test_owner_lock_writes_pid_token_and_removes_only_its_record(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "owner.lock"

    with file_lock.owner_lock(lock, wait_seconds=0) as owner:
        assert lock.read_text(encoding="ascii") == owner
        assert owner.startswith(f"{os.getpid()}:")

    assert lock.exists() is False


def test_owner_lock_times_out_instead_of_stealing_a_live_owner(tmp_path: Path) -> None:
    lock = tmp_path / "owner.lock"

    with file_lock.owner_lock(lock, wait_seconds=0):
        with pytest.raises(TimeoutError, match="owner lock"):
            with file_lock.owner_lock(lock, wait_seconds=0):
                pass


def test_owner_lock_reclaims_a_dead_pid_without_an_age_grace(tmp_path: Path) -> None:
    lock = tmp_path / "owner.lock"
    lock.write_text("2147483647:dead-owner", encoding="ascii")

    with file_lock.owner_lock(lock, wait_seconds=0) as owner:
        assert lock.read_text(encoding="ascii") == owner

    assert lock.exists() is False


def test_owner_lock_preserves_a_successor_record_on_release(tmp_path: Path) -> None:
    lock = tmp_path / "owner.lock"

    with file_lock.owner_lock(lock, wait_seconds=0):
        lock.write_text("999999:successor", encoding="ascii")

    assert lock.read_text(encoding="ascii") == "999999:successor"


@pytest.mark.parametrize("value", [-1.0, float("inf"), float("nan"), True])
def test_owner_lock_rejects_invalid_waits(tmp_path: Path, value: float) -> None:
    with pytest.raises(ValueError):
        with file_lock.owner_lock(tmp_path / "owner.lock", wait_seconds=value):
            pass


def test_agent_log_uses_extracted_primitive_without_changing_live_paths(
    tmp_path: Path,
) -> None:
    assert agent_log._owned_lock is file_lock._owned_lock
    assert agent_log._stale_record is file_lock._stale_record
    assert agent_log.agent_log_path(str(tmp_path), "codex") == (
        tmp_path.resolve() / ".smtw" / "agents" / "codex.jsonl"
    )


def test_migration_lock_is_a_root_sibling_not_inside_either_state_tree(
    tmp_path: Path,
) -> None:
    assert migration_lock_path(tmp_path) == tmp_path.resolve() / MIGRATION_LOCK_NAME
