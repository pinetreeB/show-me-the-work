"""v2.6.2 QUAR-03A/B + MIGRATION-03 (RED-first).

QUAR-03A: post-write 검증 실패 destination은 삭제(실패 시 corrupt 표시)되고
list/show에서 complete로 노출되지 않는다.
QUAR-03B: GC는 UUID 이름순이 아닌 (st_mtime_ns, name) oldest-first로 evict한다.
MIGRATION-03: archive unlink 실패는 orphan으로 남아 다음 maintenance에서
재시도되고, retention report에 실패가 표시된다.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import time

import pytest

import core.ledger_migration as migration_module
from core import quarantine
from core.quarantine import (
    ENTRY_PREFIX,
    backup_blocked_command,
    list_entries,
    quarantine_dir,
)


REASON_CODE = "r2_destructive_ambiguous"


def _backup(root: Path, command: str = "rm -rf /", **kwargs: object) -> Path | None:
    return backup_blocked_command(
        str(root),
        command=command,
        agent_key="codex_cli:quarantine-session:codex",
        reason_code=REASON_CODE,
        target="peer.py",
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# QUAR-03A — failed verification destination cleanup
# ---------------------------------------------------------------------------


def test_quar_03a_failed_verification_removes_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(quarantine, "_verify_destination", lambda *a, **k: False)

    result = _backup(tmp_path)

    assert result is None
    directory = quarantine_dir(str(tmp_path))
    leftovers = [p for p in directory.iterdir() if p.is_file()] if directory.exists() else []
    # 수정 전: 검증 실패 destination이 그대로 남아 list에 complete로 노출(RED).
    assert leftovers == []


def test_quar_03a_undeletable_failure_is_marked_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(quarantine, "_verify_destination", lambda *a, **k: False)
    monkeypatch.setattr(quarantine, "_unlink_failed_destination", lambda path: False)

    result = _backup(tmp_path)

    assert result is None
    entries = list_entries(str(tmp_path))
    assert len(entries) == 1
    assert entries[0].record_status == "corrupt"


def test_quar_03a_corrupted_body_never_lists_complete(tmp_path: Path) -> None:
    created = _backup(tmp_path)
    assert created is not None and created.is_file()
    # 유효 헤더를 유지한 채 body를 훼손한다(저장 후 손상 시뮬레이션).
    with created.open("ab") as handle:
        _ = handle.write(b"corruption")

    entries = list_entries(str(tmp_path))

    assert len(entries) == 1
    # 수정 전: 훼손된 body가 record_status=complete로 노출(RED).
    assert entries[0].record_status == "corrupt"


def test_quar_03a_valid_entry_still_lists_complete(tmp_path: Path) -> None:
    created = _backup(tmp_path)

    assert created is not None
    entries = list_entries(str(tmp_path))

    assert len(entries) == 1
    assert entries[0].record_status == "complete"


# ---------------------------------------------------------------------------
# QUAR-03B — GC oldest-first within same-timestamp burst
# ---------------------------------------------------------------------------


def test_quar_03b_gc_evicts_oldest_by_mtime_not_uuid_name_order(
    tmp_path: Path,
) -> None:
    directory = quarantine_dir(str(tmp_path))
    directory.mkdir(parents=True, exist_ok=True)
    base = time.time()
    # 같은 compact stamp(동일 초 burst) + 이름순 aaa < bbb < ccc.
    # mtime은 이름순과 반대로: aaa가 최신, ccc가 최古.
    stamps = {"aaa": base, "bbb": base - 100, "ccc": base - 200}
    for token, mtime in stamps.items():
        path = directory / f"{ENTRY_PREFIX}20200101T000000Z-agent-{token}.txt"
        path.write_text(f"payload-{token}", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    # 새 엔트리 1개 추가(max_entries=2 → 총 4개 중 최신 2개만 생존).
    _backup(tmp_path, max_entries=2)

    survivors = {p.name for p in directory.iterdir() if p.is_file()}
    aaa = f"{ENTRY_PREFIX}20200101T000000Z-agent-aaa.txt"
    bbb = f"{ENTRY_PREFIX}20200101T000000Z-agent-bbb.txt"
    ccc = f"{ENTRY_PREFIX}20200101T000000Z-agent-ccc.txt"
    # 수정 전: 이름순 eviction → aaa·bbb 삭제, ccc 생존(RED).
    # 기대: mtime oldest-first → ccc·bbb 삭제, aaa(최신 기성분) + 신규 생존.
    assert ccc not in survivors
    assert bbb not in survivors
    assert aaa in survivors
    assert len(survivors) == 2


# ---------------------------------------------------------------------------
# MIGRATION-03 — failed archive unlink retry + retention report
# ---------------------------------------------------------------------------

ARCHIVE_REASON = migration_module.INVOCATION_STATUS_ARCHIVE_REASON


def _make_archive(destination: Path, content: bytes, created_at: str) -> dict:
    digest = sha256(content).hexdigest()
    name = f"{migration_module.INVOCATION_STATUS_ARCHIVE_PREFIX}{digest}.bak"
    destination.with_name(name).write_bytes(content)
    return {
        "digest": digest,
        "path": name,
        "created_at": created_at,
        "reason": ARCHIVE_REASON,
    }


def _write_index(destination: Path, entries: list[dict]) -> Path:
    index_path = destination.with_name(
        migration_module.INVOCATION_STATUS_ARCHIVE_INDEX_NAME
    )
    index_path.write_text(
        json.dumps({"schema_version": 1, "archives": entries}),
        encoding="utf-8",
    )
    return index_path


def _read_index(destination: Path) -> list[dict]:
    index_path = destination.with_name(
        migration_module.INVOCATION_STATUS_ARCHIVE_INDEX_NAME
    )
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    archives = raw["archives"]
    assert isinstance(archives, list)
    return archives


def test_migration_03_failed_unlink_is_retried_by_next_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        migration_module, "INVOCATION_STATUS_ARCHIVE_MAX_COUNT", 1
    )
    destination = tmp_path / ".smtw" / "ledger.json"
    destination.parent.mkdir(parents=True)
    destination.write_text("{}", encoding="utf-8")
    old = _make_archive(destination, b"old-ledger-bytes", "2026-07-01T00:00:00+00:00")
    new = _make_archive(destination, b"new-ledger-bytes", "2026-07-02T00:00:00+00:00")
    _write_index(destination, [old, new])
    orphan_path = destination.with_name(old["path"])

    real_unlink = Path.unlink
    state = {"failed": False}

    def flaky_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.endswith(".bak") and not state["failed"]:
            state["failed"] = True
            raise OSError("simulated unlink failure")
        return real_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    # 1차 maintenance: unlink 실패 → victim은 orphan으로 남고 report에 표시.
    report = migration_module._enforce_invocation_status_archive_retention(
        destination, protected_digest=new["digest"]
    )

    assert isinstance(report, dict), "retention must report outcomes (None before fix)"
    assert [entry["digest"] for entry in _read_index(destination)] == [new["digest"]]
    assert orphan_path.is_file(), "failed unlink victim stays on disk for retry"
    failed = report.get("failed")
    assert isinstance(failed, list) and failed, "failed unlink must be reported"

    # 2차 maintenance(정상 unlink): orphan scan이 잔여 파일을 삭제한다.
    report2 = migration_module._enforce_invocation_status_archive_retention(
        destination, protected_digest=new["digest"]
    )

    # 수정 전: orphan이 영구 잔존(재시도 경로 없음, RED).
    assert not orphan_path.is_file()
    removed = report2.get("orphans_removed")
    assert isinstance(removed, list) and orphan_path.name in removed
    assert [entry["digest"] for entry in _read_index(destination)] == [new["digest"]]


def test_migration_03_successful_eviction_reports_no_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        migration_module, "INVOCATION_STATUS_ARCHIVE_MAX_COUNT", 1
    )
    destination = tmp_path / ".smtw" / "ledger.json"
    destination.parent.mkdir(parents=True)
    destination.write_text("{}", encoding="utf-8")
    old = _make_archive(destination, b"old-ledger-bytes", "2026-07-01T00:00:00+00:00")
    new = _make_archive(destination, b"new-ledger-bytes", "2026-07-02T00:00:00+00:00")
    _write_index(destination, [old, new])

    report = migration_module._enforce_invocation_status_archive_retention(
        destination, protected_digest=new["digest"]
    )

    assert isinstance(report, dict)
    assert not destination.with_name(old["path"]).exists()
    assert report.get("failed") == []
    assert report.get("orphans_removed") == []
    evicted = report.get("evicted")
    assert isinstance(evicted, list) and old["path"] in evicted
