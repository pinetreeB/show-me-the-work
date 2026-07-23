from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import time
from typing import Final
import uuid

from .ledger_storage import state_dir
from .state_layout import state_write_scope

QUARANTINE_DIRNAME: Final[str] = "quarantine"
ENTRY_PREFIX: Final[str] = "blocked-"

MAX_ENTRIES: Final[int] = 64
MAX_ENTRY_BYTES: Final[int] = 1 * 1024 * 1024
MAX_TOTAL_BYTES: Final[int] = 16 * 1024 * 1024
TTL_SECONDS: Final[int] = 7 * 24 * 60 * 60
UUID_RETRY_LIMIT: Final[int] = 16

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_RECORD_SEPARATOR = b"# ---\n"
_ENCODING = "utf-8"
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


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    path: Path
    id: str
    created_at: str
    agent_key: str
    reason_code: str
    target: str
    size_bytes: int
    original_bytes: int | None = None
    stored_bytes: int | None = None
    original_sha256: str = ""
    stored_sha256: str = ""
    truncated: bool | None = None
    encoding: str = ""
    record_status: str = "unknown"


@dataclass(frozen=True, slots=True)
class _EncodedRecord:
    payload: bytes
    original_bytes: int
    stored_bytes: int
    original_sha256: str
    stored_sha256: str
    truncated: bool
    encoding: str
    record_status: str


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
        with state_write_scope(project_root, wait_seconds=0) as authority:
            return _backup_blocked_command_unlocked(
                authority,
                command=command,
                agent_key=agent_key,
                reason_code=reason_code,
                target=target,
                now=now,
                max_entries=max_entries,
                max_entry_bytes=max_entry_bytes,
                max_total_bytes=max_total_bytes,
                ttl_seconds=ttl_seconds,
            )
    except Exception:  # noqa: BLE001 - a deny must never wait or fail on backup.
        return None


def _backup_blocked_command_unlocked(
    authority: Path,
    *,
    command: str,
    agent_key: str,
    reason_code: str,
    target: str,
    now: float | None,
    max_entries: int,
    max_entry_bytes: int,
    max_total_bytes: int,
    ttl_seconds: int,
) -> Path | None:
    try:
        directory = authority / QUARANTINE_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        stamp = _timestamp_compact(now)
        short_agent = _safe_agent_key(agent_key)
        encoded = _encode_record(
            command=command,
            stamp=stamp,
            agent_key=agent_key,
            reason_code=reason_code,
            target=target,
            max_entry_bytes=max_entry_bytes,
        )
        reserved = _reserve_destination(directory, stamp, short_agent)
        if reserved is None:
            return None
        destination, descriptor = reserved
        write_succeeded = True
        try:
            _write_all(descriptor, encoded.payload)
        except OSError:
            write_succeeded = False
        finally:
            try:
                os.close(descriptor)
            except OSError:
                write_succeeded = False
        if not write_succeeded:
            _safe_unlink(destination)
            return None

        try:
            os.chmod(destination, 0o600)
        except OSError:
            # Windows and unusual filesystems may not expose POSIX modes.  The
            # exclusive open above still requested owner-only permissions.
            pass

        _gc(
            directory,
            max_entries=max_entries,
            max_total_bytes=max_total_bytes,
            ttl_seconds=ttl_seconds,
            now=now,
        )
        expected_digest = hashlib.sha256(encoded.payload).hexdigest()
        if not _verify_destination(
            destination,
            expected_size=len(encoded.payload),
            expected_digest=expected_digest,
        ):
            # GC or another filesystem actor removed/replaced/corrupted the
            # just-created entry.  Never report a preservation success.
            return None
        return destination
    except Exception:  # noqa: BLE001 - backup must never break the caller.
        return None


def _encode_record(
    *,
    command: str,
    stamp: str,
    agent_key: str,
    reason_code: str,
    target: str,
    max_entry_bytes: int,
) -> _EncodedRecord:
    original = command.encode(_ENCODING)
    body_limit = max(0, max_entry_bytes)
    stored = original[:body_limit]
    if len(stored) < len(original):
        stored = stored.decode(_ENCODING, errors="ignore").encode(_ENCODING)
    truncated = stored != original
    original_sha256 = hashlib.sha256(original).hexdigest()
    stored_sha256 = hashlib.sha256(stored).hexdigest()
    record_status = "incomplete" if truncated else "complete"
    header = (
        f"# blocked_at: {stamp}\n"
        f"# agent: {_header_value(agent_key)}\n"
        f"# reason_code: {_header_value(reason_code)}\n"
        f"# target: {_header_value(target)}\n"
        f"# notice: {_NOTICE}\n"
        f"# original_bytes: {len(original)}\n"
        f"# stored_bytes: {len(stored)}\n"
        f"# original_sha256: {original_sha256}\n"
        f"# stored_sha256: {stored_sha256}\n"
        f"# truncated: {str(truncated).lower()}\n"
        f"# encoding: {_ENCODING}\n"
        f"# record_status: {record_status}\n"
        "# ---\n"
    )
    return _EncodedRecord(
        payload=header.encode(_ENCODING) + stored,
        original_bytes=len(original),
        stored_bytes=len(stored),
        original_sha256=original_sha256,
        stored_sha256=stored_sha256,
        truncated=truncated,
        encoding=_ENCODING,
        record_status=record_status,
    )


def _header_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def _reserve_destination(
    directory: Path, stamp: str, short_agent: str
) -> tuple[Path, int] | None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _attempt in range(UUID_RETRY_LIMIT):
        token = uuid.uuid4().hex
        destination = (
            directory / f"{ENTRY_PREFIX}{stamp}-{short_agent}-{token}.txt"
        )
        try:
            descriptor = os.open(destination, flags, 0o600)
        except FileExistsError:
            continue
        except OSError:
            return None
        return destination, descriptor
    return None


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    written_total = 0
    while written_total < len(view):
        written = os.write(descriptor, view[written_total:])
        if written <= 0:
            raise OSError("quarantine write made no progress")
        written_total += written
    os.fsync(descriptor)


def _verify_destination(
    path: Path, *, expected_size: int, expected_digest: str
) -> bool:
    try:
        if not path.is_file() or path.stat().st_size != expected_size:
            return False
        payload = path.read_bytes()
    except OSError:
        return False
    return (
        len(payload) == expected_size
        and hashlib.sha256(payload).hexdigest() == expected_digest
    )


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
        if line == "# ---":
            break
        if not line.startswith("#"):
            break
        if ":" not in line:
            continue
        key, _, value = line[1:].partition(":")
        fields[key.strip()] = value.strip()
    return fields


def _parse_nonnegative_int(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _parse_bool(value: str) -> bool | None:
    normalized = value.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _record_from_bytes(path: Path, payload: bytes) -> QuarantineRecord:
    header, _separator, _body = payload.partition(_RECORD_SEPARATOR)
    fields = _parse_header(header.decode(_ENCODING, errors="replace"))
    truncated = _parse_bool(fields.get("truncated", ""))
    record_status = fields.get("record_status", "")
    if record_status not in {"complete", "incomplete"}:
        record_status = (
            "incomplete"
            if truncated is True
            else "complete"
            if truncated is False
            else "unknown"
        )
    return QuarantineRecord(
        path=path,
        id=path.name,
        created_at=fields.get("blocked_at", ""),
        agent_key=fields.get("agent", ""),
        reason_code=fields.get("reason_code", ""),
        target=fields.get("target", ""),
        size_bytes=len(payload),
        original_bytes=_parse_nonnegative_int(fields.get("original_bytes", "")),
        stored_bytes=_parse_nonnegative_int(fields.get("stored_bytes", "")),
        original_sha256=fields.get("original_sha256", ""),
        stored_sha256=fields.get("stored_sha256", ""),
        truncated=truncated,
        encoding=fields.get("encoding", ""),
        record_status=record_status,
    )


def read_record(path: Path) -> QuarantineRecord | None:
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    return _record_from_bytes(path, payload)


def list_entries(project_root: str) -> list[QuarantineRecord]:
    directory = quarantine_dir(project_root)
    records: list[QuarantineRecord] = []
    for path in sorted(_entry_files(directory), key=lambda p: p.name):
        record = read_record(path)
        if record is None:
            continue
        records.append(record)
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
    loaded = load_entry(project_root, entry_id)
    return loaded[1] if loaded is not None else None


def load_entry(
    project_root: str, entry_id: str
) -> tuple[QuarantineRecord, str] | None:
    path = _resolve_entry_path(project_root, entry_id)
    if path is None:
        return None
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    return (
        _record_from_bytes(path, payload),
        payload.decode(_ENCODING, errors="replace"),
    )


def clear_entries(
    project_root: str, *, entry_id: str | None = None, clear_all: bool = False
) -> int:
    try:
        with state_write_scope(project_root) as authority:
            directory = authority / QUARANTINE_DIRNAME
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
    except OSError:
        return 0
