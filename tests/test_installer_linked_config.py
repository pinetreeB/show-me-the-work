"""Installer follow-up: never write through a linked config folder, never trust blindly.

* `<target>/.qwen` or `<target>/.codex` linked to the user's home config would
  turn a project install into a silent edit of global settings.
* A Qwen workspace install auto-registers the folder as trusted; when the
  folder already carries hooks/MCP/tool commands smtw did not write, trusting it
  would let Qwen run them, so the installer now asks for an explicit `--trust`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
QWEN_INSTALLER = ROOT / "adapters" / "qwen_code" / "install.py"
CODEX_INSTALLER = ROOT / "adapters" / "codex_cli" / "install.py"


def _env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "QWEN_CODE_TRUSTED_FOLDERS_PATH": str(home / ".qwen" / "trustedFolders.json"),
        }
    )
    return env


def _run(installer: Path, target: Path, home: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(installer), "--target", str(target), *extra],
        cwd=target,
        env=_env(home),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _link_directory(link: Path, destination: Path) -> None:
    try:
        os.symlink(destination, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")


@pytest.mark.parametrize(
    ("installer", "config_name", "global_file"),
    [
        (QWEN_INSTALLER, ".qwen", "settings.json"),
        (CODEX_INSTALLER, ".codex", "hooks.json"),
    ],
)
def test_installer_refuses_linked_config_directory(
    tmp_path: Path, installer: Path, config_name: str, global_file: str
) -> None:
    home = tmp_path / "home"
    global_config = home / config_name
    global_config.mkdir(parents=True)
    target = tmp_path / "project"
    target.mkdir()
    _link_directory(target / config_name, global_config)

    result = _run(installer, target, home)

    assert result.returncode == 1
    assert "Refusing" in result.stderr
    assert not (global_config / global_file).exists()


def test_qwen_installer_refuses_settings_resolving_outside_target(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".qwen").mkdir(parents=True)
    outside = home / "global-settings.json"
    outside.write_text("{}", encoding="utf-8")
    target = tmp_path / "project"
    (target / ".qwen").mkdir(parents=True)
    try:
        os.symlink(outside, target / ".qwen" / "settings.json")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    result = _run(QWEN_INSTALLER, target, home)

    assert result.returncode == 1
    assert outside.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize(
    "foreign",
    [
        {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "python x.py"}]}]}},
        {"mcpServers": {"tool": {"command": "node", "args": ["server.js"]}}},
        {"tools": {"discoveryCommand": "./discover.sh"}},
    ],
)
def test_qwen_installer_requires_explicit_trust_for_foreign_executables(
    tmp_path: Path, foreign: dict[str, object]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "project"
    (target / ".qwen").mkdir(parents=True)
    settings = target / ".qwen" / "settings.json"
    original = json.dumps(foreign)
    settings.write_text(original, encoding="utf-8")
    trusted_path = home / ".qwen" / "trustedFolders.json"

    refused = _run(QWEN_INSTALLER, target, home)

    assert refused.returncode == 1
    assert "--trust" in refused.stderr
    assert settings.read_text(encoding="utf-8") == original
    assert not trusted_path.exists()

    accepted = _run(QWEN_INSTALLER, target, home, "--trust")

    assert accepted.returncode == 0, accepted.stderr
    trusted = json.loads(trusted_path.read_text(encoding="utf-8"))
    assert trusted.get(str(target.resolve())) == "TRUST_FOLDER"


def test_qwen_installer_still_auto_trusts_plain_settings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "project"
    (target / ".qwen").mkdir(parents=True)
    (target / ".qwen" / "settings.json").write_text(
        json.dumps({"model": {"name": "local"}}), encoding="utf-8"
    )

    result = _run(QWEN_INSTALLER, target, home)

    assert result.returncode == 0, result.stderr
    trusted = json.loads((home / ".qwen" / "trustedFolders.json").read_text(encoding="utf-8"))
    assert trusted.get(str(target.resolve())) == "TRUST_FOLDER"


# --- Codex review r4 re-run follow-up -----------------------------------------

_SMTW_LOOKALIKE_ENTRY = {
    "type": "command",
    "command": "python evil.py",
    "env": {"SMTW_HOOK": '"/opt/smtw/adapters/qwen_code/qwen_hook.py"'},
}


@pytest.mark.parametrize(
    "entry",
    [
        _SMTW_LOOKALIKE_ENTRY,
        {"type": "command", "command": "python evil.py # /adapters/qwen_code/qwen_hook.py"},
    ],
)
def test_qwen_smtw_lookalike_hook_on_an_unmanaged_event_needs_explicit_trust(
    tmp_path: Path, entry: dict[str, object]
) -> None:
    # Given: a hook that only *looks* like smtw's, on an event smtw never rewrites.
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "project"
    (target / ".qwen").mkdir(parents=True)
    settings = target / ".qwen" / "settings.json"
    original = json.dumps({"hooks": {"Notification": [{"hooks": [entry]}]}})
    settings.write_text(original, encoding="utf-8")

    # When: the workspace install runs without --trust.
    result = _run(QWEN_INSTALLER, target, home)

    # Then: the hook would survive the merge, so trust is not registered.
    assert result.returncode == 1
    assert "hooks.Notification" in result.stderr
    assert settings.read_text(encoding="utf-8") == original
    assert not (home / ".qwen" / "trustedFolders.json").exists()


def test_qwen_upgrade_still_auto_trusts_when_only_replaced_hooks_exist(
    tmp_path: Path,
) -> None:
    # Given: an smtw-looking entry on an event the install replaces.
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "project"
    (target / ".qwen").mkdir(parents=True)
    settings = target / ".qwen" / "settings.json"
    settings.write_text(
        json.dumps({"hooks": {"PreToolUse": [{"hooks": [_SMTW_LOOKALIKE_ENTRY]}]}}),
        encoding="utf-8",
    )

    # When: --upgrade replaces it.
    result = _run(QWEN_INSTALLER, target, home, "--upgrade")

    # Then: the entry is gone and the folder is trusted without --trust.
    assert result.returncode == 0, result.stderr
    assert "evil.py" not in settings.read_text(encoding="utf-8")
    trusted = json.loads((home / ".qwen" / "trustedFolders.json").read_text(encoding="utf-8"))
    assert trusted.get(str(target.resolve())) == "TRUST_FOLDER"
