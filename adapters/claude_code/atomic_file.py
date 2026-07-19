from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sys
from typing import BinaryIO


def atomic_write(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    temporary_name, destination_name = _atomic_paths(temporary, path)
    data = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n"
    try:
        with open(temporary_name, "x", encoding="utf-8", newline="\n") as handle:
            _ = handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination_name)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _atomic_paths(temporary: Path, destination: Path) -> tuple[str, str]:
    temporary_name = os.path.normpath(os.path.abspath(os.fspath(temporary)))
    destination_name = os.path.normpath(os.path.abspath(os.fspath(destination)))
    if os.name != "nt":
        return temporary_name, destination_name
    extended = _needs_extended_path(temporary_name) or _needs_extended_path(
        destination_name
    )
    if not extended:
        return temporary_name, destination_name
    return _extended_windows_path(temporary_name), _extended_windows_path(
        destination_name
    )


def _needs_extended_path(path: str) -> bool:
    return len(path) >= 260 and not path.startswith(("\\\\?\\", "\\\\.\\"))


def _extended_windows_path(path: str) -> str:
    if path.startswith(("\\\\?\\", "\\\\.\\")):
        return path
    if path.startswith("\\\\"):
        return f"\\\\?\\UNC\\{path[2:]}"
    return f"\\\\?\\{path}"


@contextmanager
def path_lock(path: Path) -> Iterator[None]:
    lock = _lock_path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as handle:
        _ensure_lock_byte(handle)
        _acquire_lock(handle)
        try:
            yield
        finally:
            _release_lock(handle)


def lock_exists(path: Path) -> bool:
    return _lock_path(path).exists()


def remove_lock(path: Path) -> None:
    try:
        _lock_path(path).unlink(missing_ok=True)
    except OSError:
        return


def remove_if_stale(path: Path, cutoff: float) -> None:
    with path_lock(path):
        try:
            stale = path.stat().st_mtime < cutoff
        except FileNotFoundError:
            return
        if stale:
            path.unlink(missing_ok=True)


def remove(path: Path) -> None:
    if not path.exists():
        return
    with path_lock(path):
        path.unlink(missing_ok=True)


def _lock_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


def _ensure_lock_byte(handle: BinaryIO) -> None:
    _ = handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        _ = handle.write(b"\0")
        handle.flush()


if sys.platform == "win32":
    import msvcrt

    def _acquire_lock(handle: BinaryIO) -> None:
        _ = handle.seek(0)
        _ = msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)

    def _release_lock(handle: BinaryIO) -> None:
        _ = handle.seek(0)
        _ = msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _acquire_lock(handle: BinaryIO) -> None:
        _ = handle.seek(0)
        _ = fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _release_lock(handle: BinaryIO) -> None:
        _ = handle.seek(0)
        _ = fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
