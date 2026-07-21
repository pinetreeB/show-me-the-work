from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Final

from core.state_layout import RUNTIME_STATE_DIR_NAMES

from .models import Signature


HARD_PREFIXES: Final = frozenset({*RUNTIME_STATE_DIR_NAMES, ".git", ".hg", ".svn"})
SOFT_PREFIXES: Final = ("node_modules", ".venv", "venv", "__pycache__", ".pytest_cache")


def snapshot(root: Path, force_paths: frozenset[str]) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    _walk(root, root, force_paths, entries)
    return entries


def delta(
    before: dict[str, tuple[str, str]], after: dict[str, tuple[str, str]]
) -> tuple[Signature, ...]:
    signatures: list[Signature] = []
    for path in sorted(before.keys() | after.keys()):
        old = before.get(path)
        new = after.get(path)
        if old is None and new is not None:
            signatures.append(Signature(path, "create", new[1]))
        elif old is not None and new is None:
            signatures.append(Signature(path, "delete", None))
        elif old is not None and new is not None and old[0] != new[0]:
            signatures.append(Signature(path, "type_change", new[1]))
        elif old is not None and new is not None and old[1] != new[1]:
            signatures.append(Signature(path, "modify", new[1]))
    return tuple(signatures)


def _walk(root: Path, directory: Path, force_paths: frozenset[str], entries: dict[str, tuple[str, str]]) -> None:
    with os.scandir(directory) as children:
        for child in children:
            path = Path(child.path)
            relative = path.relative_to(root).as_posix()
            if _excluded(relative, force_paths):
                continue
            if child.is_symlink():
                entries[relative] = ("symlink", _digest(os.readlink(path).encode("utf-8", "surrogateescape")))
            elif child.is_dir(follow_symlinks=False):
                _walk(root, path, force_paths, entries)
            elif stat.S_ISREG(child.stat(follow_symlinks=False).st_mode):
                entries[relative] = ("regular", _file_digest(path))


def _excluded(path: str, force_paths: frozenset[str]) -> bool:
    first = path.split("/", 1)[0]
    if first in HARD_PREFIXES:
        return True
    if path in force_paths:
        return False
    if first not in SOFT_PREFIXES:
        return False
    return not any(candidate.startswith(f"{first}/") for candidate in force_paths)


def _file_digest(path: Path) -> str:
    digest = hashlib.blake2b(digest_size=32)
    with path.open("rb") as handle:
        while data := handle.read(1024 * 1024):
            digest.update(data)
    return f"blake2b-256:{digest.hexdigest()}"


def _digest(value: bytes) -> str:
    return f"blake2b-256:{hashlib.blake2b(value, digest_size=32).hexdigest()}"
