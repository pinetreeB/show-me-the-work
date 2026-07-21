from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import runpy
import tomllib
from typing import Final, TypeAlias


REPO_ROOT: Final = Path(__file__).resolve().parents[2]
DEDICATED_CONFIG_NAME: Final = ".smtw.toml"
PYPROJECT_NAME: Final = "pyproject.toml"
_CANONICAL_HEADER_RE: Final = re.compile(
    r"\[\s*tool\s*\.\s*smtw\s*\]"
)

ConfigScalar: TypeAlias = str | int | float | bool
ConfigValue: TypeAlias = ConfigScalar | list["ConfigValue"] | dict[str, "ConfigValue"]
NormalizedConfig: TypeAlias = dict[str, ConfigValue]


class ConfigLoadState(str, Enum):
    ABSENT = "ABSENT"
    VALID = "VALID"
    DECLARED_INVALID = "DECLARED_INVALID"


class ConfigSource(str, Enum):
    DEDICATED = "dedicated"
    PYPROJECT = "pyproject"
    LEGACY = "legacy"


@dataclass(frozen=True, slots=True)
class ProjectConfigLoad:
    state: ConfigLoadState
    values: Mapping[str, ConfigValue] | None
    canonical_bytes: bytes
    source: ConfigSource | None
    detail: str = ""

    @property
    def present(self) -> bool:
        return self.state is not ConfigLoadState.ABSENT

    @property
    def corrupt(self) -> bool:
        return self.state is ConfigLoadState.DECLARED_INVALID

    @property
    def raw_bytes(self) -> bytes:
        """Compatibility name for STATE-01 callers; bytes are canonical."""
        return self.canonical_bytes

    @property
    def digest(self) -> str:
        if self.state is not ConfigLoadState.VALID:
            return ""
        return sha256(self.canonical_bytes).hexdigest()


def load_project_config(root: Path) -> ProjectConfigLoad:
    """Load exactly one project config using the v3 precedence contract."""
    dedicated = _load_dedicated(root / DEDICATED_CONFIG_NAME)
    if dedicated.state is not ConfigLoadState.ABSENT:
        return dedicated

    pyproject = _load_pyproject(root / PYPROJECT_NAME)
    if pyproject.state is not ConfigLoadState.ABSENT:
        return pyproject

    legacy = _load_legacy(_legacy_config_path(root))
    if legacy.state is not ConfigLoadState.ABSENT:
        return legacy

    return _absent(None)


def project_config_present(root: Path) -> bool:
    """Return whether the selected root declares valid or invalid SMTW config."""
    return load_project_config(root).present


def config_state(root: Path) -> tuple[bool, str, bool]:
    """Return exact supervision opt-in state, canonical digest, and corruption."""
    loaded = load_project_config(root)
    if loaded.corrupt:
        return False, "", True
    raw = loaded.values
    if raw is None:
        return False, "", False
    enabled = raw["supervision"] is True
    return enabled, loaded.digest if enabled else "", False


def _load_dedicated(path: Path) -> ProjectConfigLoad:
    raw_bytes, failure = _read_config_bytes(path, ConfigSource.DEDICATED)
    if failure is not None:
        return failure
    assert raw_bytes is not None
    try:
        raw: object = tomllib.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return _invalid(ConfigSource.DEDICATED, _parse_detail(exc))
    return _validated(ConfigSource.DEDICATED, raw)


def _load_pyproject(path: Path) -> ProjectConfigLoad:
    raw_bytes, failure = _read_config_bytes(path, ConfigSource.PYPROJECT)
    if failure is not None:
        return failure
    assert raw_bytes is not None
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _invalid(ConfigSource.PYPROJECT, _parse_detail(exc))
    try:
        raw: object = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        if _has_canonical_smtw_header(text):
            return _invalid(ConfigSource.PYPROJECT, _parse_detail(exc))
        return _absent(ConfigSource.PYPROJECT)
    if not isinstance(raw, Mapping):
        return _absent(ConfigSource.PYPROJECT)
    tool = raw.get("tool")
    if not isinstance(tool, Mapping) or "smtw" not in tool:
        return _absent(ConfigSource.PYPROJECT)
    return _validated(ConfigSource.PYPROJECT, tool.get("smtw"))


def _load_legacy(path: Path) -> ProjectConfigLoad:
    raw_bytes, failure = _read_config_bytes(path, ConfigSource.LEGACY)
    if failure is not None:
        return failure
    assert raw_bytes is not None
    try:
        raw: object = json.loads(
            raw_bytes,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return _invalid(ConfigSource.LEGACY, _parse_detail(exc))
    return _validated(ConfigSource.LEGACY, raw)


def _read_config_bytes(
    path: Path,
    source: ConfigSource,
) -> tuple[bytes | None, ProjectConfigLoad | None]:
    try:
        return path.read_bytes(), None
    except FileNotFoundError:
        return None, _absent(source)
    except OSError as exc:
        return None, _invalid(source, f"cannot read: {type(exc).__name__}")


def _validated(source: ConfigSource, raw: object) -> ProjectConfigLoad:
    if not isinstance(raw, Mapping):
        return _invalid(source, "configuration must be a table/object")
    try:
        normalized_value = _normalize_value(raw)
    except (TypeError, ValueError) as exc:
        return _invalid(source, str(exc))
    if not isinstance(normalized_value, dict):
        return _invalid(source, "configuration must be a table/object")
    normalized = normalized_value
    schema = normalized.get("schema_version")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != 1:
        return _invalid(source, "schema_version must be integer 1")
    supervision = normalized.get("supervision")
    if not isinstance(supervision, bool):
        return _invalid(source, "supervision must be boolean")
    canonical_bytes = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return ProjectConfigLoad(
        ConfigLoadState.VALID,
        normalized,
        canonical_bytes,
        source,
    )


def _normalize_value(value: object) -> ConfigValue:
    if isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("configuration floats must be finite")
        return value
    if isinstance(value, list | tuple):
        return [_normalize_value(item) for item in value]
    if isinstance(value, Mapping):
        normalized: NormalizedConfig = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("configuration keys must be strings")
            normalized[key] = _normalize_value(item)
        return normalized
    raise TypeError(f"unsupported configuration value: {type(value).__name__}")


def _has_canonical_smtw_header(text: str) -> bool:
    return any(
        _CANONICAL_HEADER_RE.match(line.strip()) is not None
        for line in _toml_code_lines(text)
    )


def _toml_code_lines(text: str) -> tuple[str, ...]:
    """Strip comments and TOML strings while preserving structural code."""
    lines: list[str] = []
    multiline: str | None = None
    for line in text.splitlines():
        code: list[str] = []
        index = 0
        while index < len(line):
            if multiline is not None:
                closing = _find_multiline_close(line, index, multiline)
                if closing is None:
                    index = len(line)
                    continue
                index = closing + 3
                multiline = None
                continue

            if line.startswith('"""', index):
                multiline = '"""'
                index += 3
                continue
            if line.startswith("'''", index):
                multiline = "'''"
                index += 3
                continue
            character = line[index]
            if character == "#":
                break
            if character == '"':
                index = _skip_basic_string(line, index + 1)
                continue
            if character == "'":
                closing = line.find("'", index + 1)
                index = len(line) if closing < 0 else closing + 1
                continue
            code.append(character)
            index += 1
        lines.append("".join(code))
    return tuple(lines)


def _find_multiline_close(
    line: str,
    start: int,
    delimiter: str,
) -> int | None:
    index = start
    while True:
        found = line.find(delimiter, index)
        if found < 0:
            return None
        if delimiter == "'''" or not _is_escaped(line, found):
            return found
        index = found + 1


def _skip_basic_string(line: str, start: int) -> int:
    index = start
    while index < len(line):
        if line[index] == "\\":
            index += 2
            continue
        if line[index] == '"':
            return index + 1
        index += 1
    return len(line)


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _parse_detail(exc: Exception) -> str:
    return f"cannot parse: {type(exc).__name__}"


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"unsupported JSON constant: {value}")


def _absent(source: ConfigSource | None) -> ProjectConfigLoad:
    return ProjectConfigLoad(ConfigLoadState.ABSENT, None, b"", source)


def _invalid(source: ConfigSource, detail: str) -> ProjectConfigLoad:
    return ProjectConfigLoad(
        ConfigLoadState.DECLARED_INVALID,
        None,
        b"",
        source,
        detail,
    )


@lru_cache(maxsize=1)
def _legacy_config_names() -> tuple[str, str]:
    values = runpy.run_path(str(REPO_ROOT / "core" / "state_layout.py"))
    state_name = values.get("LEGACY_STATE_DIR_NAME")
    config_name = values.get("LEGACY_ACTIVATION_CONFIG_NAME")
    if not isinstance(state_name, str) or not isinstance(config_name, str):
        raise RuntimeError("state layout constants are unavailable")
    return state_name, config_name


def _legacy_config_path(root: Path) -> Path:
    state_name, config_name = _legacy_config_names()
    return root / state_name / config_name
