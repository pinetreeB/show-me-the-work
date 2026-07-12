# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import cast


VERSION_RE = re.compile(r"\d+\.\d+\.\d+\Z")
CHANGELOG_RE = re.compile(r"^## \[(?P<version>\d+\.\d+\.\d+)\]", re.MULTILINE)
PROJECT_SECTION_RE = re.compile(
    r"^\[project\][ \t]*\r?\n(?P<body>.*?)(?=^\[|\Z)",
    re.MULTILINE | re.DOTALL,
)
PROJECT_VERSION_RE = re.compile(
    r'^version[ \t]*=[ \t]*"(?P<version>[^"\r\n]+)"[ \t]*$',
    re.MULTILINE,
)
BADGE_VERSION_RE = re.compile(
    r"version-(?P<version>\d+\.\d+\.\d+)-brightgreen\.svg"
)


class SyncError(RuntimeError):
    pass


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SyncError(f"cannot read {path}: {exc}") from exc


def _read_json(path: Path) -> dict[str, object]:
    try:
        raw = cast(object, json.loads(_read_text(path)))
    except json.JSONDecodeError as exc:
        raise SyncError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SyncError(f"expected a JSON object in {path}")
    return cast(dict[str, object], raw)


def _strict_version(value: object, label: str) -> str:
    if not isinstance(value, str) or VERSION_RE.fullmatch(value) is None:
        raise SyncError(f"{label} must be an X.Y.Z version")
    return value


def _plugin_version(root: Path) -> str:
    path = root / ".claude-plugin" / "plugin.json"
    plugin = _read_json(path)
    return _strict_version(plugin.get("version"), str(path))


def _marketplace_text(path: Path, version: str) -> tuple[str, str]:
    current_text = _read_text(path)
    marketplace = _read_json(path)
    metadata_raw = marketplace.get("metadata")
    if not isinstance(metadata_raw, dict):
        raise SyncError(f"expected metadata object in {path}")
    metadata = cast(dict[str, object], metadata_raw)
    current_version = _strict_version(metadata.get("version"), str(path))
    if current_version == version:
        return current_text, current_text
    metadata["version"] = version
    return current_text, json.dumps(marketplace, ensure_ascii=False, indent=2) + "\n"


def _project_text(path: Path, version: str) -> tuple[str, str]:
    current_text = _read_text(path)
    sections = list(PROJECT_SECTION_RE.finditer(current_text))
    if len(sections) != 1:
        raise SyncError(f"expected one [project] section in {path}")
    section = sections[0]
    body = section.group("body")
    matches = list(PROJECT_VERSION_RE.finditer(body))
    if len(matches) != 1:
        raise SyncError(f"expected one [project] version in {path}")
    match = matches[0]
    _ = _strict_version(match.group("version"), str(path))
    start = section.start("body") + match.start("version")
    end = section.start("body") + match.end("version")
    return current_text, current_text[:start] + version + current_text[end:]


def _readme_text(path: Path, version: str) -> tuple[str, str]:
    current_text = _read_text(path)
    matches = list(BADGE_VERSION_RE.finditer(current_text))
    if len(matches) != 1:
        raise SyncError(f"expected one version badge in {path}")
    match = matches[0]
    start, end = match.span("version")
    return current_text, current_text[:start] + version + current_text[end:]


def _validate_changelog(root: Path, version: str) -> None:
    path = root / "CHANGELOG.md"
    match = CHANGELOG_RE.search(_read_text(path))
    if match is None:
        raise SyncError(f"missing release heading in {path}")
    if match.group("version") != version:
        raise SyncError(
            f"{path} latest version {match.group('version')} does not match SSOT {version}"
        )


def _plan_updates(root: Path) -> tuple[str, dict[Path, tuple[str, str]]]:
    version = _plugin_version(root)
    _validate_changelog(root, version)
    marketplace = root / ".claude-plugin" / "marketplace.json"
    pyproject = root / "pyproject.toml"
    readme = root / "README.md"
    updates = {
        marketplace: _marketplace_text(marketplace, version),
        pyproject: _project_text(pyproject, version),
        readme: _readme_text(readme, version),
    }
    korean_readme = root / "README.ko.md"
    if korean_readme.exists():
        updates[korean_readme] = _readme_text(korean_readme, version)
    return version, updates


def _atomic_write(path: Path, text: str) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            _ = handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _label(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


class Arguments(argparse.Namespace):
    root: Path = Path()
    check: bool = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize release versions from .claude-plugin/plugin.json."
    )
    _ = parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (defaults to this script's repository).",
    )
    _ = parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift without writing files.",
    )
    args = parser.parse_args(argv, namespace=Arguments())
    root = args.root.resolve()

    try:
        version, updates = _plan_updates(root)
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    drift = [path for path, (current, wanted) in updates.items() if current != wanted]
    if args.check:
        if drift:
            for path in drift:
                print(f"drift: {_label(path, root)}")
            return 1
        print(f"version {version}: synchronized")
        return 0

    try:
        for path in drift:
            _atomic_write(path, updates[path][1])
            print(f"updated: {_label(path, root)}")
    except OSError as exc:
        print(f"error: failed to update release files: {exc}", file=sys.stderr)
        return 2

    if not drift:
        print(f"version {version}: already synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
