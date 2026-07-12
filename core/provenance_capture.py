from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import BinaryIO, Final

from .provenance_types import EntryKind, ManifestEntry, ScanIssue

HASH_CHUNK_BYTES: Final = 1024 * 1024


@dataclass(frozen=True, slots=True)
class CapturedPath:
    entry: ManifestEntry | None
    issue: ScanIssue | None


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    path: Path
    relative: str
    canonical_key: str
    previous: ManifestEntry | None = None


def capture_regular(request: CaptureRequest) -> CapturedPath:
    for _ in range(2):
        try:
            before = os.stat(request.path, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                return CapturedPath(None, ScanIssue(request.relative, "unstable_path"))
            previous = request.previous
            if previous is not None and _can_reuse(previous, before):
                after = os.stat(request.path, follow_symlinks=False)
                if _stats_match(before, after):
                    return CapturedPath(_entry(request, after, previous.digest), None)
                continue
            with request.path.open("rb") as handle:
                before_fd = os.fstat(handle.fileno())
                if not _stats_match(before, before_fd):
                    continue
                digest = _digest_stream(handle)
                after_fd = os.fstat(handle.fileno())
            after = os.stat(request.path, follow_symlinks=False)
        except OSError:
            return CapturedPath(None, ScanIssue(request.relative, "unreadable_path"))
        if _stats_match(before, after_fd) and _stats_match(before, after):
            return CapturedPath(_entry(request, after, digest), None)
    return CapturedPath(None, ScanIssue(request.relative, "unstable_path"))


def capture_symlink(request: CaptureRequest) -> CapturedPath:
    for _ in range(2):
        try:
            before = os.stat(request.path, follow_symlinks=False)
            target = os.readlink(request.path)
            after = os.stat(request.path, follow_symlinks=False)
        except OSError:
            return CapturedPath(None, ScanIssue(request.relative, "unreadable_path"))
        if _stats_match(before, after):
            # Symlink targets are hashed but never traversed, so the walker cannot enter a symlink cycle.
            return CapturedPath(_entry(request, after, _digest_text(target), EntryKind.SYMLINK), None)
    return CapturedPath(None, ScanIssue(request.relative, "unstable_path"))


def _can_reuse(previous: ManifestEntry | None, metadata: os.stat_result) -> bool:
    return previous is not None and previous.file_type is EntryKind.REGULAR and (
        previous.size,
        previous.mtime_ns,
        previous.mode,
    ) == (
        metadata.st_size,
        metadata.st_mtime_ns,
        stat.S_IMODE(metadata.st_mode),
    )


def _entry(
    request: CaptureRequest,
    metadata: os.stat_result,
    digest: str,
    file_type: EntryKind = EntryKind.REGULAR,
) -> ManifestEntry:
    return ManifestEntry(
        path=request.relative,
        canonical_key=request.canonical_key,
        file_type=file_type,
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
        mode=stat.S_IMODE(metadata.st_mode),
        digest=digest,
    )


def _stats_match(before: os.stat_result, after: os.stat_result) -> bool:
    return _stat_signature(before) == _stat_signature(after)


def _stat_signature(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        stat.S_IFMT(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
        stat.S_IMODE(metadata.st_mode),
    )


def _digest_stream(handle: BinaryIO) -> str:
    digest = hashlib.blake2b(digest_size=32)
    while chunk := handle.read(HASH_CHUNK_BYTES):
        digest.update(chunk)
    return f"blake2b-256:{digest.hexdigest()}"


def _digest_text(value: str) -> str:
    digest = hashlib.blake2b(value.encode("utf-8", "surrogateescape"), digest_size=32)
    return f"blake2b-256:{digest.hexdigest()}"
