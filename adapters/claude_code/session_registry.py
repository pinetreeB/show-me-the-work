from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module
import json
import os
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapters.claude_code.atomic_file import (
        atomic_write as _atomic_write,
        lock_exists as _lock_exists,
        path_lock as _path_lock,
        remove as _remove,
        remove_if_stale as _remove_if_stale,
        remove_lock as _remove_lock,
    )
else:
    _module_prefix = "adapters.claude_code." if __package__ else ""
    _atomic_file = import_module(f"{_module_prefix}atomic_file")
    _atomic_write = _atomic_file.atomic_write
    _lock_exists = _atomic_file.lock_exists
    _path_lock = _atomic_file.path_lock
    _remove = _atomic_file.remove
    _remove_if_stale = _atomic_file.remove_if_stale
    _remove_lock = _atomic_file.remove_lock

SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
GC_MAX_ENTRIES = 128
GC_TIME_BUDGET_SECONDS = 0.025


@dataclass(frozen=True, slots=True)
class RegistryRecord:
    schema_version: int
    root: str
    config_digest: str
    created_at: str
    last_activity_at: str


@dataclass(frozen=True, slots=True)
class BindResult:
    record: RegistryRecord
    replaced_corrupt: bool
    root_mismatch: bool


@dataclass(frozen=True, slots=True)
class TurnRecord:
    schema_version: int
    prompt: str
    prompt_id: str
    mode: str
    context_emitted: bool
    updated_at: str


class QuickPromotionPersistenceError(RuntimeError):
    pass


def session_digest(session_id: str) -> str:
    return sha256(session_id.encode("utf-8")).hexdigest()


def registry_path(data_dir: Path, session_id: str) -> Path:
    return data_dir / "sessions" / f"{session_digest(session_id)}.json"


def registry_was_bound(data_dir: Path, session_id: str) -> bool:
    return _lock_exists(registry_path(data_dir, session_id))


def load_session(
    data_dir: Path,
    session_id: str,
) -> tuple[RegistryRecord | None, bool]:
    path = registry_path(data_dir, session_id)
    if not path.exists():
        return None, False
    with _path_lock(path):
        return _read_registry(path)


def bind_session(
    data_dir: Path,
    session_id: str,
    root: Path,
    config_digest: str,
) -> BindResult:
    path = registry_path(data_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _path_lock(path):
        existing, corrupt = _read_registry(path)
        now = _now()
        if existing is not None:
            mismatch = not _same_path(Path(existing.root), root)
            touched = replace(existing, last_activity_at=now)
            _atomic_write(path, asdict(touched))
            return BindResult(touched, False, mismatch)
        created = RegistryRecord(1, str(root), config_digest, now, now)
        _atomic_write(path, asdict(created))
        return BindResult(created, corrupt, False)


def save_turn(
    data_dir: Path,
    session_id: str,
    agent: str,
    prompt: str,
    prompt_id: str,
    mode: str,
) -> TurnRecord:
    path = _turn_path(data_dir, session_id, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _path_lock(path):
        existing = _read_turn(path)
        emitted = (
            existing is not None
            and existing.prompt_id == prompt_id
            and existing.context_emitted
        )
        record = TurnRecord(1, prompt, prompt_id, mode, emitted, _now())
        _atomic_write(path, asdict(record))
    return record


def load_turn(data_dir: Path, session_id: str, agent: str) -> TurnRecord | None:
    path = _turn_path(data_dir, session_id, agent)
    if not path.exists():
        return None
    with _path_lock(path):
        return _read_turn(path)


@contextmanager
def promote_quick(
    data_dir: Path,
    session_id: str,
    agent: str,
) -> Iterator[bool]:
    path = _turn_path(data_dir, session_id, agent)
    if not path.exists():
        yield False
        return
    with _path_lock(path):
        record = _read_turn(path)
        if record is None or record.mode != "quick":
            yield False
            return
        try:
            yield True
        finally:
            try:
                _atomic_write(
                    path,
                    asdict(replace(record, mode="normal", updated_at=_now())),
                )
            except (OSError, TypeError, ValueError) as exc:
                raise QuickPromotionPersistenceError(
                    "quick promotion could not be persisted"
                ) from exc


def mark_context_emitted(
    data_dir: Path,
    session_id: str,
    agent: str,
    prompt_id: str,
) -> bool:
    path = _turn_path(data_dir, session_id, agent)
    if not path.exists():
        return False
    with _path_lock(path):
        record = _read_turn(path)
        if record is None or record.prompt_id != prompt_id or record.context_emitted:
            return False
        _atomic_write(
            path,
            asdict(replace(record, context_emitted=True, updated_at=_now())),
        )
        return True


def warn_once(data_dir: Path, session_id: str, code: str) -> bool:
    prefix = session_digest(session_id)
    code_digest = sha256(code.encode("utf-8")).hexdigest()[:16]
    path = data_dir / "warnings" / f"{prefix}-{code_digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _path_lock(path):
        if path.exists():
            return False
        _atomic_write(path, {"created_at": _now(), "code": code})
        return True


def gc_stale(data_dir: Path, keep_session_id: str) -> None:
    keep = registry_path(data_dir, keep_session_id)
    cutoff = datetime.now(timezone.utc).timestamp() - SESSION_TTL_SECONDS
    deadline = monotonic() + GC_TIME_BUDGET_SECONDS
    checked = 0
    for directory in ("sessions", "turns", "warnings"):
        parent = data_dir / directory
        if not parent.exists():
            continue
        with os.scandir(parent) as entries:
            for entry in entries:
                if checked >= GC_MAX_ENTRIES or monotonic() >= deadline:
                    return
                checked += 1
                if not entry.name.endswith(".json"):
                    continue
                path = Path(entry.path)
                if path == keep:
                    continue
                existed = path.exists()
                _remove_if_stale(path, cutoff)
                if existed and not path.exists():
                    _remove_lock(path)


def cleanup_session(data_dir: Path, session_id: str) -> None:
    if not data_dir.exists():
        return
    digest = session_digest(session_id)
    paths = [registry_path(data_dir, session_id)]
    for directory in ("turns", "warnings"):
        parent = data_dir / directory
        if parent.exists():
            paths.extend(parent.glob(f"{digest}-*.json"))
    for path in paths:
        _remove(path)
        _remove_lock(path)


def _turn_path(data_dir: Path, session_id: str, agent: str) -> Path:
    agent_digest = sha256(agent.encode("utf-8")).hexdigest()[:16]
    return data_dir / "turns" / f"{session_digest(session_id)}-{agent_digest}.json"


def _read_registry(path: Path) -> tuple[RegistryRecord | None, bool]:
    raw, corrupt = _read_object(path)
    if raw is None:
        return None, corrupt
    schema = raw.get("schema_version")
    root = raw.get("root")
    digest = raw.get("config_digest")
    created = raw.get("created_at")
    activity = raw.get("last_activity_at")
    valid_schema = (
        isinstance(schema, int) and not isinstance(schema, bool) and schema == 1
    )
    if not (
        valid_schema
        and isinstance(root, str)
        and Path(root).is_absolute()
        and isinstance(digest, str)
        and bool(digest)
        and isinstance(created, str)
        and bool(created)
        and isinstance(activity, str)
        and bool(activity)
    ):
        return None, True
    return RegistryRecord(1, root, digest, created, activity), False


def _read_turn(path: Path) -> TurnRecord | None:
    raw, _ = _read_object(path)
    if raw is None:
        return None
    prompt = raw.get("prompt")
    prompt_id = raw.get("prompt_id")
    mode = raw.get("mode")
    emitted = raw.get("context_emitted")
    updated = raw.get("updated_at")
    if not (
        raw.get("schema_version") == 1
        and isinstance(prompt, str)
        and isinstance(prompt_id, str)
        and mode in {"quick", "normal", "deep"}
        and isinstance(emitted, bool)
        and isinstance(updated, str)
    ):
        return None
    return TurnRecord(1, prompt, prompt_id, str(mode), emitted, updated)


def _read_object(path: Path) -> tuple[dict[str, object] | None, bool]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, False
    except (json.JSONDecodeError, OSError):
        return None, True
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        return None, True
    return raw, False


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(
        str(right.resolve())
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
