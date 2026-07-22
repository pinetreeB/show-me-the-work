from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from adapters.codex_cli import stop as codex_stop
from core.agent_log import agent_log_path
from core.contract import (
    contract_path,
    evaluate_pretool_contract,
    namespaced_contract_path,
)
from core.intent import IntentInput, intent_path, load_intent, save_intent
from core.ledger import load_ledger, record_event
from core.ledger_storage import ledger_path
from core.provenance_policy import (
    PROVENANCE_CONFIG_NAME,
    load_provenance_config,
    provenance_config_relative_path,
)
from core.provenance import workspace_scope_policy_id
from core.provenance_store import snapshots_dir
from core.scorecard_coordination import coordination_journal_path
from core.scorecard_store import scorecard_journal_path
from core.state_layout import (
    LEGACY_STATE_DIR_NAME,
    STATE_DIR_NAME,
    StateLayout,
    inspect_state_layout,
    state_dir,
)
from core.state_migration import MigrationStatus, migrate_state
from goals import goals as goals_store


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (
    "core",
    "adapters",
    "smtw",
    "goals",
    "contrib",
    "scripts",
)


def _prepare_layout(root: Path, layout: str) -> Path:
    if layout == "legacy":
        selected = root / LEGACY_STATE_DIR_NAME
        selected.mkdir(parents=True)
        return selected
    if layout == "native":
        selected = root / STATE_DIR_NAME
        selected.mkdir(parents=True)
        return selected
    legacy = root / LEGACY_STATE_DIR_NAME
    legacy.mkdir(parents=True)
    (legacy / "seed.json").write_text("{}\n", encoding="utf-8")
    result = migrate_state(root, lock_wait_seconds=0)
    assert result.status is MigrationStatus.MIGRATED
    return root / STATE_DIR_NAME


@pytest.mark.parametrize("layout", ["legacy", "native", "migrated"])
def test_every_state_consumer_uses_one_selected_tree(
    tmp_path: Path,
    layout: str,
) -> None:
    root = tmp_path / layout
    selected = _prepare_layout(root, layout)
    agent_key = "codex_cli:session:codex"

    paths = (
        ledger_path(str(root)),
        agent_log_path(str(root), "codex"),
        snapshots_dir(root),
        contract_path(str(root)),
        namespaced_contract_path(str(root), agent_key),
        goals_store._legacy_goals_path(str(root)),
        goals_store.namespaced_goals_path(str(root), agent_key),
        intent_path(str(root)),
        scorecard_journal_path(root),
        coordination_journal_path(root),
    )

    assert state_dir(root) == selected
    assert all(path == selected or selected in path.parents for path in paths)
    assert provenance_config_relative_path(root) == (
        f"{selected.name}/{PROVENANCE_CONFIG_NAME}"
    )


def test_legacy_writers_remain_whole_tree_compatible(tmp_path: Path) -> None:
    legacy = tmp_path / LEGACY_STATE_DIR_NAME
    legacy.mkdir()

    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "legacy project",
        }
    )
    _ = save_intent(
        str(tmp_path),
        IntentInput("goal", ("app.py",), (), False, "confirmed", 0),
    )
    _ = goals_store.plan(
        str(tmp_path),
        "goal",
        "story",
        "python -m pytest",
    )

    assert ledger_path(str(tmp_path)).parent == legacy
    assert intent_path(str(tmp_path)).parent == legacy
    assert goals_store._legacy_goals_path(str(tmp_path)).parent == legacy
    assert (tmp_path / STATE_DIR_NAME).exists() is False


def test_published_tree_never_falls_back_to_legacy_files(tmp_path: Path) -> None:
    legacy = tmp_path / LEGACY_STATE_DIR_NAME
    legacy.mkdir()
    (legacy / "ledger.json").write_text(
        json.dumps({"prompt": "legacy-only"}), encoding="utf-8"
    )
    (legacy / "intent.json").write_text(
        json.dumps({"goal": "legacy-only"}), encoding="utf-8"
    )
    (legacy / PROVENANCE_CONFIG_NAME).write_text(
        json.dumps({"version": 1, "exclude": ["legacy-only/**"]}),
        encoding="utf-8",
    )
    result = migrate_state(tmp_path, lock_wait_seconds=0)
    assert result.status is MigrationStatus.MIGRATED
    target = tmp_path / STATE_DIR_NAME

    (target / "ledger.json").unlink()
    (target / "intent.json").unlink()
    (target / PROVENANCE_CONFIG_NAME).unlink()
    (legacy / "late-v2-write.json").write_text("legacy", encoding="utf-8")
    (target / "runtime-write.json").write_text("native", encoding="utf-8")

    ledger = load_ledger({"project_root": str(tmp_path)})
    config = load_provenance_config(tmp_path)
    assert inspect_state_layout(tmp_path) is StateLayout.MIGRATED
    assert state_dir(tmp_path) == target
    assert ledger.get("prompt") != "legacy-only"
    assert load_intent(str(tmp_path)) == {}
    assert config.exclude == ()
    assert config.config_relative_path == f"{STATE_DIR_NAME}/{PROVENANCE_CONFIG_NAME}"
    assert contract_path(str(tmp_path)).parent == target


def test_migration_changes_scope_identity_for_the_selected_config_path(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / LEGACY_STATE_DIR_NAME
    legacy.mkdir()
    (legacy / PROVENANCE_CONFIG_NAME).write_text(
        json.dumps({"version": 1, "exclude": ["build/**"]}),
        encoding="utf-8",
    )
    legacy_policy = workspace_scope_policy_id(tmp_path)

    result = migrate_state(tmp_path, lock_wait_seconds=0)

    assert result.status is MigrationStatus.MIGRATED
    assert workspace_scope_policy_id(tmp_path) != legacy_policy


@pytest.mark.skipif(
    os.name != "nt",
    reason="codex reaper is Windows-only; the os.name='nt' patch makes pathlib build WindowsPath, which is unavailable on POSIX",
)
def test_codex_reaper_default_log_uses_selected_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = _prepare_layout(tmp_path, "native")
    monkeypatch.setenv(codex_stop.REAPER_ENABLE_ENV, "1")
    monkeypatch.delenv(codex_stop.REAPER_LOG_ENV, raising=False)
    completed = subprocess.CompletedProcess(args=[], returncode=0)

    with (
        patch.object(codex_stop.os, "name", "nt"),
        patch.object(codex_stop.subprocess, "run", return_value=completed) as run,
    ):
        codex_stop._run_process_reaper(REPO_ROOT, str(tmp_path))

    child_env = run.call_args.kwargs["env"]
    assert Path(child_env[codex_stop.REAPER_LOG_ENV]) == (
        selected / "codex-process-reaper.log"
    )


@pytest.mark.skipif(
    os.name != "nt",
    reason="codex reaper is Windows-only; the os.name='nt' patch makes pathlib build WindowsPath, which is unavailable on POSIX",
)
def test_codex_reaper_preserves_an_explicit_empty_log_setting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(codex_stop.REAPER_ENABLE_ENV, "1")
    monkeypatch.setenv(codex_stop.REAPER_LOG_ENV, "")
    completed = subprocess.CompletedProcess(args=[], returncode=0)

    with (
        patch.object(codex_stop.os, "name", "nt"),
        patch.object(codex_stop.subprocess, "run", return_value=completed) as run,
    ):
        codex_stop._run_process_reaper(REPO_ROOT, str(tmp_path))

    child_env = run.call_args.kwargs["env"]
    assert child_env[codex_stop.REAPER_LOG_ENV] == "."


def test_conflicted_layout_blocks_writes_instead_of_falling_open(
    tmp_path: Path,
) -> None:
    (tmp_path / LEGACY_STATE_DIR_NAME).mkdir()
    (tmp_path / STATE_DIR_NAME).mkdir()

    decision = evaluate_pretool_contract(
        {
            "project_root": str(tmp_path),
            "tool_name": "Edit",
            "file_paths": ["app.py"],
            "prompt": "rename a helper",
        }
    )

    assert decision["decision"] == "block"
    assert "state layout conflict" in str(decision["reason"])


def test_production_has_no_direct_legacy_state_literal_outside_layout_ssot() -> None:
    violations: list[str] = []
    allowed_hits = 0
    for root_name in PRODUCTION_ROOTS:
        for path in (REPO_ROOT / root_name).rglob("*.py"):
            relative = path.relative_to(REPO_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(
                    node.value, str
                ):
                    continue
                if LEGACY_STATE_DIR_NAME not in node.value:
                    continue
                if (
                    relative == "core/state_layout.py"
                    and node.value == LEGACY_STATE_DIR_NAME
                ):
                    allowed_hits += 1
                    continue
                violations.append(f"{relative}:{node.lineno}:{node.value!r}")

    assert allowed_hits == 1
    assert violations == []
