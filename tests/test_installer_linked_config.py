"""Installer follow-up: never write through a linked config folder.

`<target>/.qwen` or `<target>/.codex` linked to the user's home config would
turn a project install into a silent edit of global settings.
"""

from __future__ import annotations

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
