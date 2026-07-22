from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

import core.state_layout as state_layout
from core.destructive_guard import evaluate_r2_destructive_gate
from core.file_lock import owner_lock
from core.ledger import JsonObject
from core.state_layout import (
    LEGACY_STATE_DIR_NAME,
    MIGRATION_LOCK_NAME,
    STATE_DIR_NAME,
    StateLayout,
    inspect_state_layout,
    state_dir,
)
from core.state_migration import MigrationResult, MigrationStatus, migrate_state
from goals.goals import plan


ROOT = Path(__file__).resolve().parents[1]
WRITE_COUNT = 100
WRITER_COUNT = 8


def _legacy(root: Path) -> Path:
    source = root / LEGACY_STATE_DIR_NAME
    source.mkdir(parents=True)
    (source / "ledger.json").write_text(
        json.dumps({"schema_version": 2, "active_turns": {}}),
        encoding="utf-8",
    )
    (source / "goals.json").write_text(
        json.dumps({"goal": "old", "stories": []}),
        encoding="utf-8",
    )
    return source


def _python_env() -> dict[str, str]:
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    return {
        **os.environ,
        "PYTHONPATH": python_path,
        "PYTHONIOENCODING": "utf-8",
    }


def test_goals_write_at_publish_boundary_reselects_final_authority(
    tmp_path: Path,
) -> None:
    source = _legacy(tmp_path)
    publish_reached = threading.Event()
    release_publish = threading.Event()
    migration_result: list[MigrationResult] = []
    writer_result: list[JsonObject] = []
    errors: list[BaseException] = []

    def pause_at_publish(stage: str, _path: Path | None) -> None:
        if stage == "before_publish":
            publish_reached.set()
            if not release_publish.wait(5):
                raise TimeoutError("test did not release publish")

    def migrate() -> None:
        migration_result.append(
            migrate_state(tmp_path, lock_wait_seconds=5, fault_injector=pause_at_publish)
        )

    def write_goal() -> None:
        try:
            writer_result.append(plan(str(tmp_path), "new", "story", "pytest"))
        except BaseException as exc:  # noqa: BLE001 - thread evidence is re-raised below.
            errors.append(exc)

    migration = threading.Thread(target=migrate)
    writer = threading.Thread(target=write_goal)
    migration.start()
    assert publish_reached.wait(5)
    writer.start()
    try:
        writer.join(0.15)
        assert writer.is_alive(), "writer escaped the layout barrier"
    finally:
        release_publish.set()
        migration.join(10)
        writer.join(10)

    assert not errors
    assert len(migration_result) == 1
    assert migration_result[0].status is MigrationStatus.MIGRATED
    assert len(writer_result) == 1
    assert inspect_state_layout(tmp_path) is StateLayout.MIGRATED
    target_payload = json.loads(
        (tmp_path / STATE_DIR_NAME / "goals.json").read_text(encoding="utf-8")
    )
    legacy_payload = json.loads((source / "goals.json").read_text(encoding="utf-8"))
    assert target_payload["goal"] == "new"
    assert legacy_payload["goal"] == "old"


def test_eight_process_writers_lose_no_successful_write_during_migration(
    tmp_path: Path,
) -> None:
    source = _legacy(tmp_path)
    worker_code = """
import pathlib, sys
from core.state_layout import state_write_scope
root = pathlib.Path(sys.argv[1])
writer = sys.argv[2]
for index in range(100):
    with state_write_scope(root, wait_seconds=30) as authority:
        directory = authority / "barrier-events"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{writer}-{index}.txt").write_text(
            f"{writer}:{index}", encoding="utf-8"
        )
print(100)
"""
    migration_code = """
import pathlib, time
from core.state_migration import migrate_state
root = pathlib.Path(__import__('sys').argv[1])
def pause(stage, _path):
    if stage == "before_publish":
        time.sleep(0.25)
print(migrate_state(root, lock_wait_seconds=30, fault_injector=pause).status.value)
"""
    migration = subprocess.Popen(
        [sys.executable, "-c", migration_code, str(tmp_path)],
        cwd=ROOT,
        env=_python_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    writers = [
        subprocess.Popen(
            [sys.executable, "-c", worker_code, str(tmp_path), str(writer)],
            cwd=ROOT,
            env=_python_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for writer in range(WRITER_COUNT)
    ]
    writer_outputs = [process.communicate(timeout=90) for process in writers]
    migration_stdout, migration_stderr = migration.communicate(timeout=90)

    assert migration.returncode == 0, migration_stderr
    assert migration_stdout.strip() == MigrationStatus.MIGRATED.value
    for process, output in zip(writers, writer_outputs, strict=True):
        assert process.returncode == 0, output[1]
    assert [stdout.strip() for stdout, _stderr in writer_outputs] == [
        str(WRITE_COUNT)
    ] * WRITER_COUNT
    assert all(not stderr for _stdout, stderr in writer_outputs)
    assert inspect_state_layout(tmp_path) is StateLayout.MIGRATED
    expected = {
        f"{writer}-{index}.txt"
        for writer in range(WRITER_COUNT)
        for index in range(WRITE_COUNT)
    }
    target_events = tmp_path / STATE_DIR_NAME / "barrier-events"
    actual = {path.name for path in target_events.glob("*.txt")}
    legacy_events = source / "barrier-events"
    legacy_only = (
        {path.name for path in legacy_events.glob("*.txt")} - actual
        if legacy_events.exists()
        else set()
    )
    assert actual == expected
    assert legacy_only == set()


@pytest.mark.parametrize(
    "cut",
    (
        "layout_locked",
        "after_file_copy",
        "after_marker_write",
        "after_publish",
        "receipt_write",
    ),
)
def test_hard_crash_cuts_recover_without_legacy_fallback(
    tmp_path: Path, cut: str
) -> None:
    root = tmp_path / cut
    _legacy(root)
    crash_code = """
import os, pathlib, sys
import core.state_migration as migration
from core.state_layout import MIGRATION_RECEIPT_NAME
root = pathlib.Path(sys.argv[1])
cut = sys.argv[2]
if cut == "receipt_write":
    real_write = migration._atomic_write_json
    def crash_receipt(path, payload):
        if path.name == MIGRATION_RECEIPT_NAME:
            os._exit(91)
        return real_write(path, payload)
    migration._atomic_write_json = crash_receipt
    injector = None
else:
    def crash(stage, _path):
        if stage == cut:
            os._exit(91)
    injector = crash
migration.migrate_state(root, lock_wait_seconds=5, fault_injector=injector)
sys.exit(92)
"""
    crashed = subprocess.run(
        [sys.executable, "-c", crash_code, str(root), cut],
        cwd=ROOT,
        env=_python_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )

    assert crashed.returncode == 91, crashed.stderr
    recovered = migrate_state(
        root,
        lock_wait_seconds=5,
        orphan_min_age_seconds=0,
    )
    assert recovered.status in {
        MigrationStatus.MIGRATED,
        MigrationStatus.ALREADY_MIGRATED,
    }
    scope = getattr(state_layout, "state_write_scope", None)
    assert scope is not None
    with scope(root, wait_seconds=5) as authority:
        (authority / f"recovered-{cut}.txt").write_text("ok", encoding="utf-8")
    assert inspect_state_layout(root) is StateLayout.MIGRATED
    assert state_dir(root) == root / STATE_DIR_NAME
    assert (root / STATE_DIR_NAME / f"recovered-{cut}.txt").is_file()
    assert not (root / LEGACY_STATE_DIR_NAME / f"recovered-{cut}.txt").exists()
    assert not (root / MIGRATION_LOCK_NAME).exists()


def test_r2_deny_does_not_wait_for_layout_writer_lock(tmp_path: Path) -> None:
    _legacy(tmp_path)
    payload: JsonObject = {
        "project_root": str(tmp_path),
        "tool_name": "Bash",
        "command": "git reset --hard HEAD",
        "host": "codex_cli",
        "session_id": "state-barrier",
        "agent": "codex",
    }
    with owner_lock(tmp_path / MIGRATION_LOCK_NAME, wait_seconds=0):
        started = time.perf_counter()
        result = evaluate_r2_destructive_gate(payload)
        elapsed = time.perf_counter() - started

    assert result["decision"] == "block"
    assert elapsed < 0.25
    assert not (tmp_path / LEGACY_STATE_DIR_NAME / "quarantine").exists()
