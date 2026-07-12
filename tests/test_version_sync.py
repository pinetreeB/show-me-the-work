from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
from typing import cast


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_version.py"
RELEASE_FILES = (
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "pyproject.toml",
    "README.md",
    "README.ko.md",
    "CHANGELOG.md",
)


def _write_fixture(
    root: Path,
    *,
    ssot: str = "1.2.0",
    derived: str = "1.1.3",
    changelog: str | None = None,
    valid_korean_badge: bool = True,
) -> None:
    plugin_dir = root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    _ = (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "fable-lite", "version": ssot}, indent=2) + "\n",
        encoding="utf-8",
    )
    _ = (plugin_dir / "marketplace.json").write_text(
        json.dumps(
            {"name": "fable-lite", "metadata": {"version": derived}},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _ = (root / "pyproject.toml").write_text(
        f'[build-system]\nrequires = []\n\n[project]\nname = "fable-lite"\nversion = "{derived}"\n',
        encoding="utf-8",
    )
    badge = f"[![version](https://img.shields.io/badge/version-{derived}-brightgreen.svg)](CHANGELOG.md)"
    _ = (root / "README.md").write_text(
        f"# fable-lite\n\n{badge}\n",
        encoding="utf-8",
    )
    korean_text = f"# fable-lite\n\n{badge}\n" if valid_korean_badge else "# fable-lite\n"
    _ = (root / "README.ko.md").write_text(korean_text, encoding="utf-8")
    release = ssot if changelog is None else changelog
    _ = (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{release}] - 2026-07-12\n",
        encoding="utf-8",
    )


def _run_sync(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    return {name: (root / name).read_bytes() for name in RELEASE_FILES}


def _json_object(path: Path) -> dict[str, object]:
    raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    assert isinstance(raw, dict)
    return cast(dict[str, object], raw)


def _string(value: object) -> str:
    assert isinstance(value, str)
    return value


def _versions(root: Path) -> dict[str, str]:
    plugin = _json_object(root / ".claude-plugin/plugin.json")
    marketplace = _json_object(root / ".claude-plugin/marketplace.json")
    metadata_raw = marketplace.get("metadata")
    assert isinstance(metadata_raw, dict)
    metadata = cast(dict[str, object], metadata_raw)
    pyproject_text = (root / "pyproject.toml").read_text(encoding="utf-8")
    pyproject_match = re.search(
        r'^\[project\]\s*$.*?^version\s*=\s*"(\d+\.\d+\.\d+)"',
        pyproject_text,
        re.MULTILINE | re.DOTALL,
    )
    assert pyproject_match is not None

    def badge(filename: str) -> str:
        text = (root / filename).read_text(encoding="utf-8")
        match = re.search(r"version-(\d+\.\d+\.\d+)-brightgreen\.svg", text)
        assert match is not None
        return match.group(1)

    return {
        "plugin": _string(plugin["version"]),
        "marketplace": _string(metadata["version"]),
        "pyproject": pyproject_match.group(1),
        "README.md": badge("README.md"),
        "README.ko.md": badge("README.ko.md"),
    }


def test_sync_version_check_reports_drift_without_writing(tmp_path: Path) -> None:
    # Given: the SSOT is newer than every derived version surface.
    _write_fixture(tmp_path)
    before = _snapshot(tmp_path)

    # When: check mode inspects the isolated release fixture.
    result = _run_sync(tmp_path, "--check")

    # Then: drift is reported and no bytes change.
    assert result.returncode == 1, result.stderr
    for filename in RELEASE_FILES[1:5]:
        assert filename in result.stdout
    assert _snapshot(tmp_path) == before


def test_sync_version_write_updates_all_targets_and_is_idempotent(tmp_path: Path) -> None:
    # Given: four derived files lag the plugin SSOT.
    _write_fixture(tmp_path)

    # When: write mode runs twice and check mode follows.
    first = _run_sync(tmp_path)
    after_first = _snapshot(tmp_path)
    second = _run_sync(tmp_path)
    after_second = _snapshot(tmp_path)
    check = _run_sync(tmp_path, "--check")

    # Then: all five version surfaces converge and the second run is byte-stable.
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert check.returncode == 0, check.stderr
    assert set(_versions(tmp_path).values()) == {"1.2.0"}
    assert after_second == after_first


def test_sync_version_aborts_before_writes_on_invalid_target(tmp_path: Path) -> None:
    # Given: one required existing README lacks its version badge.
    _write_fixture(tmp_path, valid_korean_badge=False)
    before = _snapshot(tmp_path)

    # When: write mode performs its preflight.
    result = _run_sync(tmp_path)

    # Then: it fails before changing any release file.
    assert result.returncode == 2
    assert "README.ko.md" in result.stderr
    assert "badge" in result.stderr.casefold()
    assert _snapshot(tmp_path) == before


def test_sync_version_aborts_before_writes_on_changelog_mismatch(tmp_path: Path) -> None:
    # Given: CHANGELOG does not lead with the plugin SSOT version.
    _write_fixture(tmp_path, changelog="1.1.3")
    before = _snapshot(tmp_path)

    # When: write mode validates the human-authored release record.
    result = _run_sync(tmp_path)

    # Then: it rejects the mismatch without partially syncing derived files.
    assert result.returncode == 2
    assert "CHANGELOG.md" in result.stderr
    assert _snapshot(tmp_path) == before
