from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "smtw", *args],
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_canonical_module_version_matches_pyproject() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"(?P<version>[^"]+)"', text, re.MULTILINE)
    assert match is not None

    result = _run("version")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == match.group("version")
    assert result.stderr == ""


def test_canonical_module_help_uses_smtw_program_name() -> None:
    result = _run("--help")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage: smtw ")
    assert "fable_lite" not in result.stdout
