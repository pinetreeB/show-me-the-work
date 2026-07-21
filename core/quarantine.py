from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Final

from .ledger_storage import state_dir

QUARANTINE_DIRNAME: Final[str] = "quarantine"
ENTRY_PREFIX: Final[str] = "blocked-"

MAX_ENTRIES: Final[int] = 64
MAX_ENTRY_BYTES: Final[int] = 1 * 1024 * 1024
MAX_TOTAL_BYTES: Final[int] = 16 * 1024 * 1024
TTL_SECONDS: Final[int] = 7 * 24 * 60 * 60

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_NOTICE = (
    "로컬 전용, 절대 커밋/전송 금지 / local-only, never commit or transmit"
)


def quarantine_dir(project_root: str) -> Path:
    return state_dir(project_root) / QUARANTINE_DIRNAME


def _safe_agent_key(agent_key: str) -> str:
    slug = _SAFE_KEY_RE.sub("-", agent_key).strip("-") or "agent"
    digest = hashlib.sha256(agent_key.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def _timestamp_compact(now: float | None = None) -> str:
    moment = time.gmtime(now if now is not None else time.time())
    return time.strftime("%Y%m%dT%H%M%SZ", moment)


def _unique_filename(directory: Path, stamp: str, short_agent: str) -> str:
    base = f"{ENTRY_PREFIX}{stamp}-{short_agent}"
    candidate = f"{base}.txt"
    counter = 1
    while (directory / candidate).exists():
        candidate = f"{base}-{counter}.txt"
        counter += 1
    return candidate


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    path: Path
    id: str
    created_at: str
    agent_key: str
    reason_code: str
    target: str
    size_bytes: int


def backup_blocked_command(
    project_root: str,
    *,
    command: str,
    agent_key: str,
    reason_code: str,
    target: str,
    now: float | None = None,
    max_entries: int = MAX_ENTRIES,
    max_entry_bytes: int = MAX_ENTRY_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    ttl_seconds: int = TTL_SECONDS,
) -> Path | None:
    """차단된 명령 원문을 로컬 quarantine 파일로 best-effort 백업한다.

    어떤 예외도 밖으로 던지지 않는다 — 실패하면 None을 반환할 뿐, 호출부의 R2
    deny 판정에는 절대 영향을 주지 않는다(fail-open, but 게이트 판정 불변).
    """
    try:
        directory = quarantine_dir(project_root)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = _timestamp_compact(now)
        short_agent = _safe_agent_key(agent_key)
        filename = _unique_filename(directory, stamp, short_agent)
        destination = directory / filename
        header = (
            f"# blocked_at: {stamp}\n"
            f"# agent: {agent_key}\n"
            f"# reason_code: {reason_code}\n"
            f"# target: {target}\n"
            f"# notice: {_NOTICE}\n"
            "# ---\n"
        )
        encoded_header = header.encode("utf-8")
        encoded_body = command.encode("utf-8")
        budget = max(0, max_entry_bytes - len(encoded_header))
        if len(encoded_body) > budget:
            encoded_body = encoded_body[:budget]
        encoded = encoded_header + encoded_body

        handle = tempfile.NamedTemporaryFile(
            "wb",
            delete=False,
            dir=directory,
            prefix="tmp-",
            suffix=".txt",
        )
        temp_name = handle.name
        try:
            with handle:
                _ = handle.write(encoded)
            os.replace(temp_name, destination)
        except OSError:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
            return None

        _gc(
            directory,
            max_entries=max_entries,
            max_total_bytes=max_total_bytes,
            ttl_seconds=ttl_seconds,
            now=now,
        )
        if not destination.exists():
            # GC could in principle race-evict the entry we just wrote (e.g. an
            # absurdly small max_entries=0 in tests) -- report that faithfully.
            return None
        return destination
    except Exception:  # noqa: BLE001 - backup must never break the caller.
        return None


def _entry_files(directory: Path) -> list[Path]:
    try:
        return [
            p
            for p in directory.iterdir()
            if p.is_file() and p.name.startswith(ENTRY_PREFIX)
        ]
    except OSError:
        return []


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _gc(
    directory: Path,
    *,
    max_entries: int = MAX_ENTRIES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    ttl_seconds: int = TTL_SECONDS,
    now: float | None = None,
) -> None:
    reference = now if now is not None else time.time()
    # 파일명이 ISO8601-compact 타임스탬프로 시작하므로 이름순 정렬이 곧 시간순이다.
    entries = sorted(_entry_files(directory), key=lambda p: p.name)

    kept: list[Path] = []
    for path in entries:
        try:
            age = reference - path.stat().st_mtime
        except OSError:
            continue
        if age > ttl_seconds:
            _safe_unlink(path)
            continue
        kept.append(path)

    while len(kept) > max_entries:
        _safe_unlink(kept.pop(0))

    sized: list[tuple[Path, int]] = []
    for path in kept:
        try:
            sized.append((path, path.stat().st_size))
        except OSError:
            sized.append((path, 0))
    total = sum(size for _, size in sized)
    index = 0
    while total > max_total_bytes and index < len(sized):
        path, size = sized[index]
        _safe_unlink(path)
        total -= size
        index += 1


def _parse_header(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        if ":" not in line:
            continue
        key, _, value = line[1:].partition(":")
        fields[key.strip()] = value.strip()
    return fields


def list_entries(project_root: str) -> list[QuarantineRecord]:
    directory = quarantine_dir(project_root)
    records: list[QuarantineRecord] = []
    for path in sorted(_entry_files(directory), key=lambda p: p.name):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            size = path.stat().st_size
        except OSError:
            continue
        fields = _parse_header(text)
        records.append(
            QuarantineRecord(
                path=path,
                id=path.name,
                created_at=fields.get("blocked_at", ""),
                agent_key=fields.get("agent", ""),
                reason_code=fields.get("reason_code", ""),
                target=fields.get("target", ""),
                size_bytes=size,
            )
        )
    return records


def _resolve_entry_path(project_root: str, entry_id: str) -> Path | None:
    directory = quarantine_dir(project_root)
    if not entry_id or "/" in entry_id or "\\" in entry_id:
        return None
    candidate = directory / entry_id
    try:
        directory_resolved = directory.resolve()
        resolved = candidate.resolve()
    except OSError:
        return None
    if resolved.parent != directory_resolved:
        return None
    if not resolved.name.startswith(ENTRY_PREFIX):
        return None
    if not resolved.is_file():
        return None
    return resolved


def show_entry(project_root: str, entry_id: str) -> str | None:
    path = _resolve_entry_path(project_root, entry_id)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def clear_entries(
    project_root: str, *, entry_id: str | None = None, clear_all: bool = False
) -> int:
    directory = quarantine_dir(project_root)
    if clear_all:
        count = 0
        for path in _entry_files(directory):
            _safe_unlink(path)
            count += 1
        return count
    if entry_id:
        path = _resolve_entry_path(project_root, entry_id)
        if path is None:
            return 0
        _safe_unlink(path)
        return 1
    return 0
