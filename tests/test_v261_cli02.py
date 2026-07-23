from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ENV_KEYS = (
    "SMTW_AUTO_MIGRATION",
    "FABLE_LITE_AUTO_MIGRATION",
    "SMTW_CODEX_REAPER_LOG",
    "FABLE_LITE_CODEX_REAPER_LOG",
)
DOCTOR_FIELDS = {
    "tool_version",
    "distribution_version",
    "module_path",
    "python_version",
    "python_path",
    "project_root",
    "host",
    "plugin_registration",
    "activation_status",
    "config_source",
    "config_digest",
    "runtime_env_source",
    "env_conflict",
    "state_layout",
    "authoritative_state_dir",
    "migration_readiness",
    "active_turns",
    "open_invocations",
    "ledger_health",
    "provenance_health",
    "quarantine_count",
    "quarantine_bytes",
    "last_probe_receipt",
    "host_support_status",
    "exit_code",
}


def _run(
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for key in RUNTIME_ENV_KEYS:
        environment.pop(key, None)
    if env is not None:
        environment.update(env)
    return subprocess.run(
        [sys.executable, "-m", "smtw", *args],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _active_config(root: Path) -> None:
    (root / ".smtw.toml").write_text(
        "schema_version = 1\nsupervision = true\n",
        encoding="utf-8",
    )


def test_doctor_json_reports_every_contract_field_without_secret_values(
    tmp_path: Path,
) -> None:
    _active_config(tmp_path)
    secret = "do-not-print-this-secret"

    result = _run(
        "doctor",
        "--root",
        str(tmp_path),
        "--host",
        "codex_cli",
        "--json",
        env={"SMTW_CODEX_REAPER_LOG": secret},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert DOCTOR_FIELDS <= payload.keys()
    assert payload["activation_status"] == "active"
    assert payload["state_layout"] == "EMPTY"
    assert payload["authoritative_state_dir"].endswith(".smtw")
    assert payload["config_source"] == "dedicated"
    assert len(payload["config_digest"]) == 64
    assert payload["runtime_env_source"] == "canonical"
    assert payload["env_conflict"] is False
    assert secret not in result.stdout


def test_doctor_human_output_has_all_operator_labels(tmp_path: Path) -> None:
    _active_config(tmp_path)

    result = _run("doctor", "--root", str(tmp_path), "--host", "claude_code")

    assert result.returncode == 0, result.stderr
    for label in (
        "Tool version:",
        "Distribution version:",
        "Module path:",
        "Python:",
        "Project root:",
        "Host:",
        "Plugin registration:",
        "Activation:",
        "Config source:",
        "Config digest:",
        "Runtime env source:",
        "Env conflict:",
        "State layout:",
        "Authority:",
        "Migration readiness:",
        "Active turns:",
        "Open invocations:",
        "Ledger health:",
        "Provenance health:",
        "Quarantine:",
        "Last probe receipt:",
        "Host support:",
    ):
        assert label in result.stdout


@pytest.mark.parametrize(
    ("config", "env", "expected_code", "expected_status"),
    [
        ("", {}, 2, "inactive"),
        ("schema_version = 2\nsupervision = true\n", {}, 1, "unsafe"),
        (
            "schema_version = 1\nsupervision = true\n",
            {"SMTW_AUTO_MIGRATION": "1", "FABLE_LITE_AUTO_MIGRATION": "0"},
            1,
            "unsafe",
        ),
    ],
)
def test_doctor_exit_codes_distinguish_inactive_and_unsafe(
    tmp_path: Path,
    config: str,
    env: dict[str, str],
    expected_code: int,
    expected_status: str,
) -> None:
    if config:
        (tmp_path / ".smtw.toml").write_text(config, encoding="utf-8")

    result = _run("doctor", "--root", str(tmp_path), "--json", env=env)

    assert result.returncode == expected_code
    payload = json.loads(result.stdout)
    assert payload["status"] == expected_status
    assert payload["exit_code"] == expected_code
    assert "SMTW_AUTO_MIGRATION" not in result.stdout or "1" not in result.stdout


def test_status_is_short_and_reports_runtime_fields(tmp_path: Path) -> None:
    _active_config(tmp_path)

    result = _run("status", "--root", str(tmp_path), "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "active",
        "layout",
        "authority",
        "current_turn",
        "block_counters",
        "verification_freshness",
        "coordination_degraded",
        "exit_code",
    }
    assert payload["active"] is True
    assert payload["current_turn"] == "none"


def test_init_creates_config_and_gitignore_without_runtime_state(
    tmp_path: Path,
) -> None:
    result = _run("init", "--root", str(tmp_path), "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["result"] == "initialized"
    assert (tmp_path / ".smtw.toml").read_text(encoding="utf-8") == (
        "schema_version = 1\nsupervision = true\n"
    )
    ignored = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "/.smtw/" in ignored
    assert "/.fable-lite/" in ignored
    assert "smtw doctor" in payload["next_step"]
    assert not (tmp_path / ".smtw").exists()
    assert not (tmp_path / ".fable-lite").exists()


def test_init_refuses_exact_home_and_does_not_write(tmp_path: Path) -> None:
    result = _run(
        "init",
        "--root",
        str(tmp_path),
        "--json",
        env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
    )

    assert result.returncode == 2
    assert json.loads(result.stdout)["result"] == "exact_home_refused"
    assert not (tmp_path / ".smtw.toml").exists()
    assert not (tmp_path / ".gitignore").exists()


def test_init_never_overwrites_existing_or_legacy_config(tmp_path: Path) -> None:
    existing = "schema_version = 1\nsupervision = false\n"
    (tmp_path / ".smtw.toml").write_text(existing, encoding="utf-8")

    configured = _run(
        "init",
        "--root",
        str(tmp_path),
        "--no-gitignore",
        "--json",
    )

    assert configured.returncode == 0
    assert json.loads(configured.stdout)["result"] == "already_configured"
    assert (tmp_path / ".smtw.toml").read_text(encoding="utf-8") == existing

    other = tmp_path / "legacy"
    legacy = other / ".fable-lite" / "config.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        '{"schema_version":1,"supervision":true}',
        encoding="utf-8",
    )
    detected = _run(
        "init",
        "--root",
        str(other),
        "--no-gitignore",
        "--json",
    )

    assert detected.returncode == 2
    payload = json.loads(detected.stdout)
    assert payload["result"] == "legacy_config_detected"
    assert "smtw migrate" in payload["next_step"]
    assert not (other / ".smtw.toml").exists()
    assert legacy.exists()


def test_init_can_append_pyproject_config_without_rewriting_existing_content(
    tmp_path: Path,
) -> None:
    original = '[project]\nname = "demo"\n'
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(original, encoding="utf-8")

    result = _run(
        "init",
        "--root",
        str(tmp_path),
        "--config",
        "pyproject",
        "--no-gitignore",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    updated = pyproject.read_text(encoding="utf-8")
    assert updated.startswith(original)
    assert updated.count("[tool.smtw]") == 1
    assert "schema_version = 1" in updated
    assert "supervision = true" in updated
    assert not (tmp_path / ".gitignore").exists()


def test_migrate_check_is_write_free_and_human_migrate_reports_contract(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / ".fable-lite"
    legacy.mkdir()
    (legacy / "artifact.txt").write_text("legacy", encoding="utf-8")
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    checked = _run(
        "migrate",
        "--root",
        str(tmp_path),
        "--check",
        "--json",
        "--lock-wait-seconds",
        "0",
    )

    assert checked.returncode == 0, checked.stderr
    payload = json.loads(checked.stdout)
    assert payload["current_layout"] == "LEGACY"
    assert payload["result"] == "READY"
    assert payload["files"] == 1
    assert payload["bytes"] == len(b"legacy")
    assert payload["active_turn"] == "none"
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before
    assert not (tmp_path / ".smtw").exists()
    assert not (tmp_path / ".smtw-migration.lock").exists()

    migrated = _run(
        "migrate",
        "--root",
        str(tmp_path),
        "--lock-wait-seconds",
        "0",
    )

    assert migrated.returncode == 0, migrated.stderr
    for label in (
        "Current layout: LEGACY",
        "Active turn: none",
        "Files: 1",
        "Bytes: 6",
        "Result: MIGRATED",
        "Authority: .smtw",
        "Legacy retained: .fable-lite",
    ):
        assert label in migrated.stdout
    assert legacy.exists()
    assert (tmp_path / ".smtw").is_dir()


def test_migrate_check_reports_active_turn_without_writes(tmp_path: Path) -> None:
    legacy = tmp_path / ".fable-lite"
    legacy.mkdir()
    (legacy / "ledger.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "active_turns": {
                    "codex_cli:session:codex": {
                        "turn_id": "turn",
                        "invocations": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = _run("migrate", "--root", str(tmp_path), "--check", "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["result"] == "DEFERRED"
    assert payload["active_turn"] == "1"
    assert not (tmp_path / ".smtw").exists()
    assert not (tmp_path / ".smtw-migration-receipt.json").exists()
