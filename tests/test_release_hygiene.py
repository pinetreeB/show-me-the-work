from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypeAlias, cast


ROOT = Path(__file__).resolve().parents[1]

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def read_json(path: Path) -> dict[str, JsonValue]:
    raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    assert isinstance(raw, dict)
    return cast(dict[str, JsonValue], raw)


def string_value(value: JsonValue) -> str:
    assert isinstance(value, str)
    return value


def object_value(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def latest_changelog_version() -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(r"^## \[(\d+\.\d+\.\d+)\]", text, re.MULTILINE)
    assert match is not None
    return match.group(1)


def pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(
        r'^\[project\]\s*$.*?^version\s*=\s*"(\d+\.\d+\.\d+)"',
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def readme_badge_version(filename: str) -> str:
    text = (ROOT / filename).read_text(encoding="utf-8")
    match = re.search(r"version-(\d+\.\d+\.\d+)-brightgreen\.svg", text)
    assert match is not None
    return match.group(1)


def test_release_versions_are_synchronized() -> None:
    plugin = read_json(ROOT / ".claude-plugin" / "plugin.json")
    marketplace = read_json(ROOT / ".claude-plugin" / "marketplace.json")
    metadata = object_value(marketplace["metadata"])

    version = string_value(plugin["version"])
    versions = {
        "plugin": version,
        "marketplace": string_value(metadata["version"]),
        "pyproject": pyproject_version(),
        "README.md": readme_badge_version("README.md"),
        "README.ko.md": readme_badge_version("README.ko.md"),
        "CHANGELOG.md": latest_changelog_version(),
    }

    assert set(versions.values()) == {version}, versions
