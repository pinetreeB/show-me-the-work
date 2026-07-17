from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile


def plugin_data_dir(force: bool) -> Path:
    configured = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if force:
        test_name = os.environ.get("PYTEST_CURRENT_TEST") or os.getcwd()
        seed = f"{test_name}\0{os.getppid()}"
        digest = sha256(seed.encode("utf-8")).hexdigest()
        return Path(tempfile.gettempdir()) / "show-me-the-work-tests" / digest
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        base = os.environ.get("XDG_DATA_HOME")
        root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "show-me-the-work" / "claude-code"


def environment_root(payload: Mapping[str, object]) -> Path | None:
    value = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    return _resolved(value, payload) if value else None


def fallback_root(
    payload: Mapping[str, object],
    event_name: str,
    force: bool,
) -> Path | None:
    if not force and event_name != "UserPromptSubmit":
        return None
    cwd = _string(payload.get("cwd")) or _string(payload.get("project_root"))
    start = _resolved(cwd or os.getcwd(), payload)
    if force:
        return start
    for candidate in (start, *start.parents):
        if (candidate / ".fable-lite" / "config.json").exists():
            return candidate
    return start


def config_state(root: Path) -> tuple[bool, str, bool]:
    path = root / ".fable-lite" / "config.json"
    try:
        raw_bytes = path.read_bytes()
        raw: object = json.loads(raw_bytes)
    except FileNotFoundError:
        return False, "", False
    except (json.JSONDecodeError, OSError):
        return False, "", True
    if not isinstance(raw, dict):
        return False, "", False
    schema = raw.get("schema_version")
    valid_schema = (
        isinstance(schema, int) and not isinstance(schema, bool) and schema == 1
    )
    enabled = valid_schema and raw.get("supervision") is True
    digest = sha256(raw_bytes).hexdigest() if enabled else ""
    return enabled, digest, False


def is_exact_home(root: Path) -> bool:
    return os.path.normcase(str(root.resolve())) == os.path.normcase(
        str(Path.home().resolve())
    )


def _resolved(value: str, payload: Mapping[str, object]) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        cwd = _string(payload.get("cwd")) or os.getcwd()
        candidate = Path(cwd) / candidate
    return candidate.resolve()


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""
