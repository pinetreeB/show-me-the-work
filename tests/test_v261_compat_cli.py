from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_source_checkout_version_wins_over_stale_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import smtw.cli as cli

    monkeypatch.setattr(cli, "_installed_version", lambda _name: "0.0.1")

    assert cli.package_version() == _pyproject_version()


def test_doctor_warns_about_module_distribution_version_mismatch(
    tmp_path: Path,
) -> None:
    (tmp_path / ".smtw.toml").write_text(
        "schema_version = 1\nsupervision = true\n",
        encoding="utf-8",
    )
    code = """
import json
import smtw.versioning as versioning
versioning._installed_version = lambda _name: "0.0.1"
versioning._distribution_path = lambda: "/old/site-packages"
from smtw.cli import main
raise SystemExit(main())
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            "doctor",
            "--root",
            str(tmp_path),
            "--json",
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["tool_version"] == _pyproject_version()
    assert payload["distribution_version"] == "0.0.1"
    assert payload["version_mismatch"] is True
    assert any("mismatch" in warning for warning in payload["warnings"])


@pytest.mark.parametrize(
    ("module", "expected_command"),
    [
        ("cli", "{version,check,brief"),
        ("scorecard", "scorecard"),
        ("migrate", "migrate"),
    ],
)
def test_legacy_submodule_execution_uses_physical_thin_shim(
    module: str,
    expected_command: str,
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", f"fable_lite.{module}", "--help"],
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert "loader for smtw." not in result.stderr
    assert "found in sys.modules" not in result.stderr
    assert expected_command in result.stdout
