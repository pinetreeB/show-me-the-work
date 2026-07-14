from __future__ import annotations

import json
import os
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Final, TypeAlias

from .provenance_types import ProvenanceConfig, ProvenanceConfigError, ProvenancePathError

HARD_EXCLUDES: Final = (
    ".codegraph/**",
    ".git/**",
    ".fable-lite/**",
    ".fablize/**",
    ".hg/**",
    ".svn/**",
)
HARD_EXCLUDE_DIRS: Final = frozenset(
    (".codegraph", ".git", ".fable-lite", ".fablize", ".hg", ".svn")
)
SOFT_EXCLUDES: Final = (
    "node_modules/**",
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
)
SOFT_EXCLUDE_DIRS: Final = frozenset(pattern.removesuffix("/**") for pattern in SOFT_EXCLUDES)
CONFIG_RELATIVE_PATH: Final = ".fable-lite/provenance-config.json"
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | Sequence["JsonValue"] | Mapping[str, "JsonValue"]


def normalize_relative_path(root: Path, path: Path) -> str:
    root_text = os.path.abspath(root)
    path_text = os.path.abspath(path)
    try:
        relative = os.path.relpath(path_text, root_text)
    except ValueError as exc:
        raise ProvenancePathError(path=str(path), root=str(root)) from exc
    normalized = relative.replace("\\", "/")
    if normalized == ".." or normalized.startswith("../") or normalized == ".":
        raise ProvenancePathError(path=str(path), root=str(root))
    return normalized


def canonical_manifest_key(path: str, windows: bool) -> str:
    normalized = path.replace("\\", "/")
    return normalized.casefold() if windows else normalized


def load_provenance_config(root: Path) -> ProvenanceConfig:
    config_path = root / CONFIG_RELATIVE_PATH
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ProvenanceConfig()
    except OSError as exc:
        raise ProvenanceConfigError("config", f"cannot read: {exc.strerror or exc}") from exc
    try:
        raw: JsonValue = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ProvenanceConfigError("config", "must be valid JSON") from exc
    if not isinstance(raw, dict):
        raise ProvenanceConfigError("config", "must be an object")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ProvenanceConfigError("version", "must equal 1")
    return ProvenanceConfig(
        include=_patterns(raw.get("include"), "include"),
        exclude=_patterns(raw.get("exclude"), "exclude"),
        generated=_patterns(raw.get("generated"), "generated"),
    )


@lru_cache(maxsize=262_144)
def is_path_in_scope(path: str, config: ProvenanceConfig) -> bool:
    if path == CONFIG_RELATIVE_PATH:
        return True
    if is_hard_excluded(path):
        return False
    if _matches_any(path, config.include):
        return True
    if _matches_any(path, config.exclude):
        return False
    return not _has_soft_excluded_segment(path)


def is_hard_excluded(path: str) -> bool:
    return is_harness_state_path(path) or _first_segment(path) in {".git", ".hg", ".svn"}


def is_harness_state_path(path: str) -> bool:
    return _first_segment(path) in {".codegraph", ".fable-lite", ".fablize"}


@lru_cache(maxsize=65_536)
def should_descend(path: str, config: ProvenanceConfig) -> bool:
    if path == CONFIG_RELATIVE_PATH.partition("/")[0]:
        return True
    if is_hard_excluded(path):
        return False
    if _matches_any(path, config.include):
        return True
    if _matches_any(path, config.exclude) or _has_soft_excluded_segment(path):
        return _has_included_descendant(path, config.include)
    return True


def _patterns(value: JsonValue | None, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProvenanceConfigError(field, "must be a list of relative patterns")
    patterns: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise ProvenanceConfigError(f"{field}[{index}]", "must be a non-empty string")
        normalized = item.replace("\\", "/")
        if normalized.startswith("/") or normalized.startswith("../") or ":" in normalized:
            raise ProvenanceConfigError(f"{field}[{index}]", "must be root-relative")
        patterns.append(normalized)
    return tuple(patterns)


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_matches(path, pattern) for pattern in patterns)


def _first_segment(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.partition("/")[0]


def _has_soft_excluded_segment(path: str) -> bool:
    return any(segment in SOFT_EXCLUDE_DIRS for segment in path.split("/"))


def _matches(path: str, pattern: str) -> bool:
    directory = pattern.removesuffix("/**")
    return fnmatchcase(path, pattern) or path == directory


def _has_included_descendant(directory: str, patterns: tuple[str, ...]) -> bool:
    return any(_include_can_match_descendant(directory, pattern) for pattern in patterns)


def _include_can_match_descendant(directory: str, pattern: str) -> bool:
    fixed_prefix = _fixed_prefix(pattern)
    if not fixed_prefix:
        return True
    if fixed_prefix == directory or fixed_prefix.startswith(f"{directory}/"):
        return True
    return fixed_prefix != pattern and directory.startswith(f"{fixed_prefix}/")


def _fixed_prefix(pattern: str) -> str:
    segments = pattern.split("/")
    prefix: list[str] = []
    for segment in segments:
        if any(character in segment for character in "*?["):
            break
        prefix.append(segment)
    return "/".join(prefix)
