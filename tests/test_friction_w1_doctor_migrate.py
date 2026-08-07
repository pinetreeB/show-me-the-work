"""W1 — doctor 정직화(unreadable/invalid 분리) + 명시적 invocation status backfill.

friction-fix v2.7.0 W1 계약 (RED-first).

- doctor: unreadable(IO/JSON)과 invalid(스키마 필드)를 분리하고 위반 필드·
  needs_migration·다음 안전 명령을 보고한다. exit 1 유지.
- `smtw migrate --invocation-statuses [--check]`: 현행 lease-aware backfill을
  재사용한다. --check는 write-free. unsafe row(증거 없는 status-less)는 절대
  자동 close하지 않고 apply는 exit 1 + 원본/archive 무변경.
"""
from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

import core.ledger_migration as migration_module
from core.ledger import default_v2_ledger
from core.ledger_schema import validate_v2_ledger
from core.ledger_storage import ledger_path
from core.ledger_v2 import apply_v2_event
from core.state_layout import state_dir
from smtw.doctor import diagnostic_snapshot
from smtw.migrate import run_migrate


ARCHIVE_INDEX_NAME = "ledger.v2-invocation-status.index.json"
ARCHIVE_PREFIX = "ledger.v2-invocation-status."
TURN_KEY = "codex_cli:friction-w1:codex"


def _activate(root: Path) -> None:
    (root / ".smtw.toml").write_text(
        "schema_version = 1\nsupervision = true\n", encoding="utf-8"
    )


def _write_raw_ledger(root: Path, raw: str) -> None:
    authority = state_dir(str(root))
    authority.mkdir(parents=True, exist_ok=True)
    (authority / "ledger.json").write_text(raw, encoding="utf-8")


def _write_json_ledger(root: Path, payload: object) -> None:
    _write_raw_ledger(root, json.dumps(payload, ensure_ascii=False))


def _statusless_v2_ledger(*, started_at: str, evidence: bool) -> dict:
    ledger = default_v2_ledger()
    _ = apply_v2_event(
        ledger,
        {
            "event": "prompt",
            "seq": 1,
            "host": "codex_cli",
            "session_id": "friction-w1",
            "agent": "codex",
            "turn_id": "friction-turn",
            "prompt": "w1 status backfill",
        },
    )
    turns = ledger["active_turns"]
    assert isinstance(turns, dict)
    turn = turns[TURN_KEY]
    assert isinstance(turn, dict)
    row: dict = {"started_at": started_at}
    if evidence:
        row["candidate_paths"] = ["app.py"]
        row["started_seq"] = 1
    turn["invocations"] = {"tool:statusless": row}
    return ledger


def _write_statusless_v2(
    root: Path,
    *,
    started_at: str,
    evidence: bool,
) -> tuple[Path, bytes]:
    destination = ledger_path(str(root))
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = json.dumps(
        _statusless_v2_ledger(started_at=started_at, evidence=evidence),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    destination.write_bytes(source)
    return destination, source


def _archive_path(destination: Path, source: bytes) -> Path:
    return destination.with_name(
        f"{ARCHIVE_PREFIX}{sha256(source).hexdigest()}.bak"
    )


def _run_status_migrate(root: Path, *, check: bool) -> int:
    args = Namespace(
        root=str(root),
        invocation_statuses=True,
        check=check,
        lock_wait_seconds=15.0,
        json=False,
    )
    return run_migrate(args)


# ---------------------------------------------------------------------------
# doctor — unreadable vs invalid 분리 + 위반 필드·needs_migration·안전 명령
# ---------------------------------------------------------------------------


def test_doctor_distinguishes_unreadable_from_invalid_and_reports_schema_field(
    tmp_path: Path,
) -> None:
    # malformed JSON은 IO/JSON 계층 → unreadable.
    _write_raw_ledger(tmp_path, "{not-json")
    _activate(tmp_path)
    unreadable = diagnostic_snapshot(str(tmp_path))
    assert unreadable["ledger_health"] == "unreadable"
    assert unreadable["exit_code"] == 1
    assert unreadable["ledger_error_field"] is None
    assert unreadable["needs_migration"] is False

    # valid JSON의 missing schema field → invalid + 정확한 필드 경로.
    _write_json_ledger(tmp_path, {"schema_version": 2, "active_turns": {}})
    _activate(tmp_path)
    invalid = diagnostic_snapshot(str(tmp_path))
    assert invalid["ledger_health"] == "invalid"
    assert invalid["exit_code"] == 1
    assert isinstance(invalid["ledger_error_field"], str)
    assert invalid["ledger_error_field"].startswith("ledger.")


def test_doctor_reports_statusless_invocation_as_invalid_with_needs_migration(
    tmp_path: Path,
) -> None:
    _write_json_ledger(tmp_path, _statusless_v2_ledger(
        started_at="2020-01-01T00:00:00+00:00", evidence=True
    ))
    _activate(tmp_path)

    snapshot = diagnostic_snapshot(str(tmp_path))

    assert snapshot["ledger_health"] == "invalid"
    assert snapshot["exit_code"] == 1
    assert "invocations" in snapshot["ledger_error_field"]
    assert "status" in snapshot["ledger_error_field"]
    assert snapshot["needs_migration"] is True
    # W5(2026-08-07): 구 안내였던 `smtw migrate --invocation-statuses --check`는
    # status-less row에 started_at이 없으면 언제나 unsafe로 판정되어 **실행해도 아무것도
    # 풀 수 없는 막다른 명령**이었다(실측: required=209, safe=0, unsafe=209). 진단이
    # 막다른 길을 권하지 않도록 실제 복구 경로인 maintenance plan을 안내한다. plan은
    # status-less 수·영향 턴·제외 사유를 모두 보고하므로 구 명령의 진단 정보를 포함한다.
    assert snapshot["next_safe_command"] == ["smtw maintenance plan"]
    # 진단은 실제 개수를 정직하게 보고해야 한다. 형식 오류에서 0을 반환하면
    # "활성 턴 없음"으로 오독되어 진단이 스스로를 오진한다.
    assert snapshot["active_turns"] >= 1


def test_doctor_healthy_v2_ledger_needs_no_migration(tmp_path: Path) -> None:
    _write_json_ledger(tmp_path, default_v2_ledger())
    _activate(tmp_path)

    snapshot = diagnostic_snapshot(str(tmp_path))

    assert snapshot["ledger_health"] == "healthy"
    assert snapshot["exit_code"] == 0
    assert snapshot["needs_migration"] is False
    assert snapshot["next_safe_command"] == []


# ---------------------------------------------------------------------------
# migrate --invocation-statuses --check: write-free + required/safe/unsafe 요약
# ---------------------------------------------------------------------------


def test_status_migration_check_is_write_free_and_reports_summary(
    tmp_path: Path,
) -> None:
    # unsafe row: 최근 시작 + lease 증거(started_seq/candidate_paths) 없음.
    destination, source = _write_statusless_v2(
        tmp_path,
        started_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        evidence=False,
    )
    expected_digest = sha256(source).hexdigest()

    exit_code = _run_status_migrate(tmp_path, check=True)

    assert exit_code == 1
    assert destination.read_bytes() == source
    assert not destination.with_name(ARCHIVE_INDEX_NAME).exists()
    assert not list(destination.parent.glob(f"{ARCHIVE_PREFIX}*.bak"))

    inspection = migration_module.inspect_v2_invocation_statuses(str(tmp_path))
    assert inspection["required"] == 1
    assert inspection["safe"] == 0
    assert inspection["unsafe"] == 1
    assert inspection["source_digest"] == expected_digest


def test_status_migration_check_all_safe_exits_zero_and_is_write_free(
    tmp_path: Path,
) -> None:
    # safe row: 만료된 시작 + lease 증거 있음 → closed로 backfill 가능.
    destination, source = _write_statusless_v2(
        tmp_path,
        started_at="2020-01-01T00:00:00+00:00",
        evidence=True,
    )

    exit_code = _run_status_migrate(tmp_path, check=True)

    assert exit_code == 0
    assert destination.read_bytes() == source
    assert not destination.with_name(ARCHIVE_INDEX_NAME).exists()
    assert not list(destination.parent.glob(f"{ARCHIVE_PREFIX}*.bak"))


# ---------------------------------------------------------------------------
# migrate --invocation-statuses(apply): unsafe는 절대 자동 close 금지
# ---------------------------------------------------------------------------


def test_statusless_invocation_without_lease_evidence_never_auto_closes(
    tmp_path: Path,
) -> None:
    destination, source = _write_statusless_v2(
        tmp_path,
        started_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        evidence=False,
    )

    exit_code = _run_status_migrate(tmp_path, check=False)

    assert exit_code == 1
    assert destination.read_bytes() == source
    assert not destination.with_name(ARCHIVE_INDEX_NAME).exists()
    assert not list(destination.parent.glob(f"{ARCHIVE_PREFIX}*.bak"))
    assert not _archive_path(destination, source).exists()


def test_stale_statusless_invocation_with_lease_evidence_backfills_closed(
    tmp_path: Path,
) -> None:
    destination, source = _write_statusless_v2(
        tmp_path,
        started_at="2020-01-01T00:00:00+00:00",
        evidence=True,
    )

    exit_code = _run_status_migrate(tmp_path, check=False)

    assert exit_code == 0
    persisted = json.loads(destination.read_text(encoding="utf-8"))
    invocations = persisted["active_turns"][TURN_KEY]["invocations"]
    assert invocations["tool:statusless"]["status"] == "closed"
    assert _archive_path(destination, source).read_bytes() == source
    assert validate_v2_ledger(persisted) is persisted