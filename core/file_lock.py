from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import errno
import math
import os
from pathlib import Path
import time
from uuid import uuid4


LOCK_WAIT_SECONDS = 15.0
STALE_LOCK_SECONDS = 10.0


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
                    raise TimeoutError(f"timed out waiting for file guard: {path}") from exc
                time.sleep(0.01)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def read_owner_record(path: Path) -> str | None:
    try:
        encoded = path.read_bytes()
    except OSError:
        return None
    try:
        return encoded.decode("ascii").strip()
    except UnicodeDecodeError:
        return f"malformed:{encoded.hex()}"


def owner_pid(record: str) -> int | None:
    pid_text, separator, token = record.partition(":")
    if not separator or not token or not pid_text.isdigit():
        return None
    pid = int(pid_text)
    return pid if pid > 0 else None


def pid_is_alive(pid: int) -> bool:
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


def stale_owner_record(
    path: Path,
    *,
    stale_after_seconds: float = STALE_LOCK_SECONDS,
) -> str | None:
    record = read_owner_record(path)
    if record is None:
        return None
    pid = owner_pid(record)
    if pid is not None:
        return None if pid_is_alive(pid) else record
    try:
        old_enough = time.time() - path.stat().st_mtime > stale_after_seconds
    except OSError:
        return None
    return record if old_enough else None


def unlink_matching_owner(path: Path, expected: str) -> bool:
    if read_owner_record(path) != expected:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def acquire_owner_lock(
    path: Path,
    deadline: float,
    owner: str,
    *,
    stale_after_seconds: float = STALE_LOCK_SECONDS,
) -> int:
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
            stale = stale_owner_record(
                path, stale_after_seconds=stale_after_seconds
            )
            if stale is not None and unlink_matching_owner(path, stale):
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for owner lock: {path}") from exc
            time.sleep(0.01)
    encoded = owner.encode("ascii")
    _ = os.write(descriptor, encoded)
    os.fsync(descriptor)
    return descriptor


@contextmanager
def owner_lock(
    path: str | Path,
    *,
    wait_seconds: float = LOCK_WAIT_SECONDS,
    stale_after_seconds: float = STALE_LOCK_SECONDS,
    release_wait_seconds: float = 1.0,
) -> Iterator[str]:
    lock_path = Path(path)
    wait = _nonnegative_seconds(wait_seconds, "wait_seconds")
    stale_after = _nonnegative_seconds(
        stale_after_seconds, "stale_after_seconds"
    )
    release_wait = _nonnegative_seconds(
        release_wait_seconds, "release_wait_seconds"
    )
    owner = f"{os.getpid()}:{uuid4().hex}"
    descriptor = acquire_owner_lock(
        lock_path,
        time.monotonic() + wait,
        owner,
        stale_after_seconds=stale_after,
    )
    try:
        yield owner
    finally:
        os.close(descriptor)
        release_owner_lock(lock_path, owner, wait_seconds=release_wait)


@contextmanager
def _owned_lock(
    path: Path,
    deadline: float,
    *,
    release_wait_seconds: float = 1.0,
) -> Iterator[None]:
    owner = f"{os.getpid()}:{uuid4().hex}"
    descriptor = acquire_owner_lock(path, deadline, owner)
    try:
        yield
    finally:
        os.close(descriptor)
        release_owner_lock(path, owner, wait_seconds=release_wait_seconds)


def release_owner_lock(
    path: Path,
    owner: str,
    *,
    wait_seconds: float = 1.0,
) -> None:
    deadline = time.monotonic() + wait_seconds
    while True:
        if unlink_matching_owner(path, owner) or not path.exists():
            return
        current = read_owner_record(path)
        if current is not None and current != owner:
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(0.005)


def _nonnegative_seconds(value: float, field: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return float(value)


# Compatibility aliases retained while core.agent_log's private test surface migrates.
_read_lock_record = read_owner_record
_record_pid = owner_pid
_pid_is_alive = pid_is_alive
_stale_record = stale_owner_record
_unlink_matching_record = unlink_matching_owner
_acquire_owner_lock = acquire_owner_lock
_release_owner_lock = release_owner_lock
