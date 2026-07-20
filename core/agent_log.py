from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
import errno
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

from .ledger_schema import JsonObject, JsonValue
LOCK_WAIT_SECONDS = 15.0
STALE_LOCK_SECONDS = 10.0
TEST_LOCK_WAIT_ENV = "FABLE_LITE_TEST_LOCK_WAIT_SECONDS"


class _LedgerTransaction:
    """Opaque proof that the root ledger lock is active in this call stack."""

    __slots__ = ("_active", "_pid", "_root", "_thread_id")

    def __init__(self, root: Path) -> None:
        self._root = root
        self._active = True
        self._pid = os.getpid()
        self._thread_id = threading.get_ident()

    def assert_active_for(self, project_root: str) -> None:
        if not self._active:
            raise RuntimeError("ledger transaction is no longer active")
        if os.getpid() != self._pid or threading.get_ident() != self._thread_id:
            raise RuntimeError("ledger transaction cannot cross a process or thread boundary")
        if Path(project_root).resolve() != self._root:
            raise RuntimeError("ledger transaction belongs to a different project root")

    def _deactivate(self) -> None:
        self._active = False


def _safe_agent_name(agent: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", agent).strip(".-") or "agent"


def agent_log_path(project_root: str, agent: str) -> Path:
    return Path(project_root).resolve() / ".fable-lite" / "agents" / f"{_safe_agent_name(agent)}.jsonl"


@contextmanager
def _posix_guard(path: Path, deadline: float) -> Iterator[None]:
    import fcntl

    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for ledger guard: {path}") from exc
                time.sleep(0.01)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _read_lock_record(path: Path) -> str | None:
    try:
        encoded = path.read_bytes()
    except OSError:
        return None
    try:
        return encoded.decode("ascii").strip()
    except UnicodeDecodeError:
        return f"malformed:{encoded.hex()}"


def _record_pid(record: str) -> int | None:
    pid_text, separator, token = record.partition(":")
    if not separator or not token or not pid_text.isdigit():
        return None
    pid = int(pid_text)
    return pid if pid > 0 else None


def _pid_is_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name != "posix":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    import _winapi

    try:
        handle = _winapi.OpenProcess(0x1000, False, pid)
    except OSError as exc:
        return exc.winerror != 87
    try:
        return _winapi.GetExitCodeProcess(handle) == _winapi.STILL_ACTIVE
    finally:
        _winapi.CloseHandle(handle)


def _stale_record(path: Path) -> str | None:
    record = _read_lock_record(path)
    if record is None:
        return None
    pid = _record_pid(record)
    if pid is not None:
        return None if _pid_is_alive(pid) else record
    try:
        old_enough = time.time() - path.stat().st_mtime > STALE_LOCK_SECONDS
    except OSError:
        return None
    return record if old_enough else None


def _unlink_matching_record(path: Path, expected: str) -> bool:
    if _read_lock_record(path) != expected:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def _acquire_owner_lock(path: Path, deadline: float, owner: str) -> int:
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except (FileExistsError, PermissionError) as exc:
            if isinstance(exc, PermissionError) and not path.exists():
                time.sleep(0.01)
                try:
                    descriptor = os.open(
                        path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600
                    )
                except PermissionError:
                    raise exc
                except FileExistsError:
                    pass
                if descriptor is not None:
                    continue
            stale = _stale_record(path)
            if stale is not None and _unlink_matching_record(path, stale):
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for ledger lock: {path}") from exc
            time.sleep(0.01)
    encoded = owner.encode("ascii")
    _ = os.write(descriptor, encoded)
    os.fsync(descriptor)
    return descriptor


@contextmanager
def _owned_lock(
    path: Path,
    deadline: float,
    *,
    release_wait_seconds: float = 1.0,
) -> Iterator[None]:
    owner = f"{os.getpid()}:{uuid4().hex}"
    descriptor = _acquire_owner_lock(path, deadline, owner)
    try:
        yield
    finally:
        os.close(descriptor)
        _release_owner_lock(path, owner, wait_seconds=release_wait_seconds)


def _release_owner_lock(
    path: Path,
    owner: str,
    *,
    wait_seconds: float = 1.0,
) -> None:
    deadline = time.monotonic() + wait_seconds
    while True:
        if _unlink_matching_record(path, owner) or not path.exists():
            return
        current = _read_lock_record(path)
        if current is not None and current != owner:
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(0.005)


@contextmanager
def ledger_transaction(
    project_root: str,
    *,
    lock_wait_seconds: float | None = None,
    release_wait_seconds: float | None = None,
) -> Iterator[_LedgerTransaction]:
    """Hold the root ledger lock; zero wait makes one immediate acquire attempt."""
    root = Path(project_root).resolve()
    directory = root / ".fable-lite"
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "ledger.lock"
    wait_seconds = (
        _lock_wait_seconds()
        if lock_wait_seconds is None
        else _explicit_nonnegative_seconds(lock_wait_seconds, "lock_wait_seconds")
    )
    release_seconds = (
        1.0
        if release_wait_seconds is None
        else _explicit_nonnegative_seconds(
            release_wait_seconds,
            "release_wait_seconds",
        )
    )
    deadline = time.monotonic() + wait_seconds
    transaction = _LedgerTransaction(root)
    if os.name == "posix":
        with _posix_guard(directory / "ledger.guard", deadline):
            with _owned_lock(
                lock_path,
                deadline,
                release_wait_seconds=release_seconds,
            ):
                try:
                    yield transaction
                finally:
                    transaction._deactivate()
    else:
        with _owned_lock(
            lock_path,
            deadline,
            release_wait_seconds=release_seconds,
        ):
            try:
                yield transaction
            finally:
                transaction._deactivate()


def _lock_wait_seconds() -> float:
    test_value = os.environ.get(TEST_LOCK_WAIT_ENV)
    if test_value is None:
        return LOCK_WAIT_SECONDS
    try:
        wait_seconds = float(test_value)
    except ValueError:
        return LOCK_WAIT_SECONDS
    return wait_seconds if wait_seconds > 0 else LOCK_WAIT_SECONDS


def _explicit_nonnegative_seconds(value: float, field: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return float(value)


def _json_safe(value: JsonValue) -> bool:
    if isinstance(value, str | int | float | bool) or value is None:
        return True
    if isinstance(value, list):
        return all(_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_safe(item) for key, item in value.items())
    return False


def _json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return None


def _event_payload(payload: Mapping[str, JsonValue]) -> JsonObject:
    return {
        key: _json_value(value)
        for key, value in payload.items()
        if _json_safe(value)
    }


def _normalize_agent_event(event: JsonObject) -> JsonObject:
    if event.get("schema_version") == 2:
        return event
    event["legacy_event"] = True
    return event


def append_agent_event(
    project_root: str,
    agent: str,
    payload: Mapping[str, JsonValue],
) -> None:
    if not agent:
        return
    path = agent_log_path(project_root, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = _event_payload(payload)
    event["agent"] = agent
    event["timestamp"] = datetime.now(UTC).isoformat()
    try:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            _ = handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
            _ = handle.write("\n")
    except OSError:
        return


def load_agent_events(project_root: str, agent: str) -> list[JsonObject] | None:
    path = agent_log_path(project_root, agent)
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    events: list[JsonObject] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            raw: JsonValue = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _json_safe(raw):
            event = _json_value(raw)
            if isinstance(event, dict):
                events.append(_normalize_agent_event(event))
    return events
