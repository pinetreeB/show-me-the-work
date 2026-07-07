from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypeAlias


ROOT = Path(__file__).resolve().parents[1]

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def read_json(path: Path) -> dict[str, JsonValue]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return {str(key): value for key, value in raw.items()}


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


def test_release_versions_are_synchronized() -> None:
    plugin = read_json(ROOT / ".claude-plugin" / "plugin.json")
    marketplace = read_json(ROOT / ".claude-plugin" / "marketplace.json")
    metadata = object_value(marketplace["metadata"])

    version = string_value(plugin["version"])

    assert version == string_value(metadata["version"])
    assert version == latest_changelog_version()
