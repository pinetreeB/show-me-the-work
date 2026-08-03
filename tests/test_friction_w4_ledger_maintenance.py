"""friction-fix v2.7.0 W4a — ledger maintenance + journal compact (RED-first).

계약:
- timestamp-less legacy turn은 lazy GC가 보존하고 maintenance plan에서
  manual-only로 분류한다(safe-auto로 자동 정리 금지).
- plan 이후 ledger가 한 byte라도 바뀌면 apply가 digest CAS로 거부되고
  원본/backup을 보존한다.
- current identity·30분 lease 내 open invocation·unsettled attribution·
  pending manifest tx는 후보에서 제외한다.
- closed-turn replay guard 256 cap을 밀어내는 apply는 거부한다.
- journal compact가 최소 필드/count/byte cap을 만족하면서 generation·pending·
  agent-log 증거를 보존한다.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

import core.ledger_maintenance as maintenance_module
import core.provenance_manifest as provenance_manifest
from core.agent_log import append_agent_event, load_agent_events
from core.ledger_storage import ledger_path
from core.ledger_v2 import (
    MAX_CLOSED_TURNS,
    apply_v2_event,
    default_v2_ledger,
)


def _write_ledger(root: Path, ledger: dict) -> Path:
    path = ledger_path(str(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ledger, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _read_ledger(root: Path) -> dict:
    return json.loads(ledger_path(str(root)).read_text(encoding="utf-8"))


def _seed_turn(
    ledger: dict,
    agent: str,
    *,
    seq: int,
    timestamp: datetime | None = None,
    field: str = "work",
) -> str:
    event: dict = {
        "event": "prompt",
        "seq": seq,
        "host": "host",
        "session_id": f"session-{agent}",
        "agent": agent,
        "turn_id": f"turn-{agent}",
        "prompt": f"{agent} {field}",
        "baseline_snapshot_id": "snapshot:base",
        "current_snapshot_id": "snapshot:base",
    }
    if timestamp is not None:
        event["timestamp"] = timestamp.isoformat()
    _ = apply_v2_event(ledger, event)
    return f"host:session-{agent}:{agent}"


def _strip_last_event_at(ledger: dict, agent_key: str) -> None:
    turn = ledger["active_turns"][agent_key]
    assert isinstance(turn, dict)
    _ = turn.pop("last_event_at", None)
    _ = turn.pop("started_at", None)


def _make_stale(ledger: dict, *agent_keys: str, hours: int = 25) -> None:
    """시드 후 명시적으로 last_event_at을 stale로 만든다(시드 중 GC 회피)."""
    stale = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    for agent_key in agent_keys:
        turn = ledger["active_turns"][agent_key]
        assert isinstance(turn, dict)
        turn["last_event_at"] = stale
        turn["started_at"] = stale


def _ledger_fernet(root: Path) -> bytes:
    return ledger_path(str(root)).read_bytes()


# ---------------------------------------------------------------------------
# timestamp-less legacy turn: lazy GC 보존 + plan manual-only 분류
# ---------------------------------------------------------------------------


def test_timestampless_legacy_turn_is_preserved_by_lazy_gc(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    ledger = default_v2_ledger()
    _seed_turn(ledger, "old", seq=1, timestamp=now - timedelta(hours=25))
    _seed_turn(ledger, "legacy", seq=2, timestamp=now - timedelta(hours=25))
    _strip_last_event_at(ledger, "host:session-legacy:legacy")

    # 다음 트랜잭션(신규 turn)이 24h lazy GC를 트리거한다.
    _seed_turn(ledger, "fresh", seq=3, timestamp=now)

    turns = ledger["active_turns"]
    assert "host:session-old:old" not in turns  # timestamped stale → collected
    assert "host:session-legacy:legacy" in turns  # timestamp-less → preserved


def test_timestampless_legacy_turn_is_manual_only_in_plan(tmp_path: Path) -> None:
    ledger = default_v2_ledger()
    _seed_turn(ledger, "default", seq=1)
    _seed_turn(ledger, "legacy", seq=2)
    ledger["agent"] = "default"  # current identity 명시
    _strip_last_event_at(ledger, "host:session-legacy:legacy")
    _write_ledger(tmp_path, ledger)

    plan = maintenance_module.plan_maintenance(str(tmp_path))

    legacy = [
        c for c in plan.candidates if c.agent_key == "host:session-legacy:legacy"
    ]
    assert len(legacy) == 1
    assert legacy[0].category == "manual_only"
    safe = [c for c in plan.candidates if c.category == "safe_auto"]
    assert all(c.agent_key != "host:session-legacy:legacy" for c in safe)


# ---------------------------------------------------------------------------
# digest CAS: plan 후 ledger 변경 시 apply 거부 + 원본/backup 보존
# ---------------------------------------------------------------------------


def test_plan_source_digest_mismatch_rejects_apply_and_preserves_backup(
    tmp_path: Path,
) -> None:
    ledger = default_v2_ledger()
    _seed_turn(ledger, "default", seq=1)
    _seed_turn(ledger, "stale", seq=2)
    ledger["agent"] = "default"  # current identity 명시
    _make_stale(ledger, "host:session-stale:stale")
    _write_ledger(tmp_path, ledger)

    plan = maintenance_module.plan_maintenance(str(tmp_path))
    # plan 이후 ledger가 1바이트라도 바뀐다.
    ledger2 = _read_ledger(tmp_path)
    ledger2["extra_field"] = "touched"
    _write_ledger(tmp_path, ledger2)
    before = _ledger_fernet(tmp_path)

    result = maintenance_module.apply_maintenance_plan(str(tmp_path), plan)

    assert result.rejected is True
    assert _ledger_fernet(tmp_path) == before  # 원본 보존
    if result.backup_path is not None:
        assert Path(result.backup_path).exists()  # backup 보존


def test_apply_matching_digest_creates_backup_and_removes_safe_auto(
    tmp_path: Path,
) -> None:
    ledger = default_v2_ledger()
    _seed_turn(ledger, "default", seq=1)
    _seed_turn(ledger, "stale", seq=2)
    ledger["agent"] = "default"  # current identity 명시
    _make_stale(ledger, "host:session-stale:stale")
    _write_ledger(tmp_path, ledger)
    plan = maintenance_module.plan_maintenance(str(tmp_path))
    original = _ledger_fernet(tmp_path)

    result = maintenance_module.apply_maintenance_plan(str(tmp_path), plan)

    assert result.rejected is False
    assert result.removed >= 1
    assert result.backup_path is not None and Path(result.backup_path).exists()
    assert Path(result.backup_path).read_bytes() == original
    after = _read_ledger(tmp_path)
    assert "host:session-stale:stale" not in after["active_turns"]
    assert "host:session-default:default" in after["active_turns"]


# ---------------------------------------------------------------------------
# current identity / open lease / unsettled attribution / pending tx 제외
# ---------------------------------------------------------------------------


def test_plan_excludes_current_identity_turn(tmp_path: Path) -> None:
    ledger = default_v2_ledger()
    _seed_turn(ledger, "current", seq=1)
    _seed_turn(ledger, "stale", seq=2)
    ledger["agent"] = "current"  # current identity 명시
    _make_stale(ledger, "host:session-current:current", "host:session-stale:stale")
    stale_key = "host:session-stale:stale"
    _write_ledger(tmp_path, ledger)

    plan = maintenance_module.plan_maintenance(str(tmp_path))

    keys = {c.agent_key for c in plan.candidates}
    assert "host:session-current:current" not in keys  # current identity 제외
    assert stale_key in keys


def test_plan_excludes_open_lease_invocation(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    ledger = default_v2_ledger()
    _seed_turn(ledger, "default", seq=1)
    _seed_turn(ledger, "live", seq=2)
    _seed_turn(ledger, "stale", seq=3)
    ledger["agent"] = "default"  # current identity 명시
    _make_stale(ledger, "host:session-live:live", "host:session-stale:stale")
    turn = ledger["active_turns"]["host:session-live:live"]
    turn["invocations"] = {
        "tool:x": {"status": "open", "started_at": now.isoformat()}
    }
    _write_ledger(tmp_path, ledger)

    plan = maintenance_module.plan_maintenance(str(tmp_path))

    keys = {c.agent_key for c in plan.candidates}
    assert "host:session-live:live" not in keys  # lease 내 open invocation 제외
    assert "host:session-stale:stale" in keys


def test_plan_excludes_unsettled_attribution_turn(tmp_path: Path) -> None:
    ledger = default_v2_ledger()
    _seed_turn(ledger, "default", seq=1)
    _seed_turn(ledger, "unsettled", seq=2)
    _seed_turn(ledger, "stale", seq=3)
    ledger["agent"] = "default"  # current identity 명시
    _make_stale(ledger, "host:session-unsettled:unsettled", "host:session-stale:stale")
    ledger["path_attribution"] = {
        "app.py": {
            "generation": 1,
            "status": "exclusive",
            "owners": [
                {
                    "agent_key": "host:session-unsettled:unsettled",
                    "turn_id": "turn-unsettled",
                    "revision_seq": 1,
                    "after_digest": "digest:x",
                    "invocation_id": "x",
                    "settled": False,
                }
            ],
        }
    }
    _write_ledger(tmp_path, ledger)

    plan = maintenance_module.plan_maintenance(str(tmp_path))

    keys = {c.agent_key for c in plan.candidates}
    assert "host:session-unsettled:unsettled" not in keys
    assert "host:session-stale:stale" in keys


def test_pending_manifest_transaction_blocks_plan_and_apply(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    ledger = default_v2_ledger()
    _seed_turn(ledger, "stale", seq=1, timestamp=now - timedelta(hours=25))
    ledger["manifest_pending"] = {
        "base_generation": 0,
        "target_generation": 1,
        "snapshot_before": "snapshot:before",
        "snapshot_after": "snapshot:after",
        "events": [],
    }
    _write_ledger(tmp_path, ledger)
    plan = maintenance_module.plan_maintenance(str(tmp_path))
    before = _ledger_fernet(tmp_path)

    # pending tx는 plan과 apply를 모두 막는다.
    assert plan.blocked
    result = maintenance_module.apply_maintenance_plan(str(tmp_path), plan)
    assert result.rejected is True
    assert _ledger_fernet(tmp_path) == before


# ---------------------------------------------------------------------------
# closed-turn replay guard 256 cap을 밀어내는 apply 거부
# ---------------------------------------------------------------------------


def test_apply_rejects_when_closed_turn_capacity_would_be_exceeded(
    tmp_path: Path,
) -> None:
    ledger = default_v2_ledger()
    _seed_turn(ledger, "default", seq=1)
    _seed_turn(ledger, "stale", seq=2)
    ledger["agent"] = "default"  # current identity 명시
    _make_stale(ledger, "host:session-stale:stale")
    # closed_turns를 이미 256 cap 가득 채운다.
    ledger["closed_turns"] = [
        {
            "agent_key": f"host:session-full:{i}",
            "turn_id": f"turn-{i}",
            "closed_seq": i,
        }
        for i in range(MAX_CLOSED_TURNS)
    ]
    _write_ledger(tmp_path, ledger)
    plan = maintenance_module.plan_maintenance(str(tmp_path))
    before = _ledger_fernet(tmp_path)

    result = maintenance_module.apply_maintenance_plan(str(tmp_path), plan)

    assert result.rejected is True
    assert _ledger_fernet(tmp_path) == before  # 256 cap 밀어내면 거부, 원본 보존


# ---------------------------------------------------------------------------
# journal compact: 최소 필드 + count/byte cap + 증거 보존
# ---------------------------------------------------------------------------


def test_journal_compact_projects_minimal_fields_and_preserves_generation(
    tmp_path: Path,
) -> None:
    ledger = default_v2_ledger()
    event = {
        "event": "change",
        "event_id": "inv:change:src",
        "seq": 1,
        "manifest_generation": 5,
        "commit_state": "uncommitted",
        "paths": [{"path": "app.py", "op": "write"}],
        "agent": "codex",
        "project_root": str(tmp_path),
    }
    provenance_manifest._append_journal(ledger, (event,))

    journal = ledger["manifest_event_journal"]
    assert len(journal) == 1
    entry = journal[0]
    assert set(entry.keys()) == {
        "event_id",
        "seq",
        "manifest_generation",
        "commit_state",
    }
    assert entry["event_id"] == "inv:change:src"
    assert entry["seq"] == 1
    assert entry["manifest_generation"] == 5  # generation 보존
    assert entry["commit_state"] == "uncommitted"


def test_journal_compact_enforces_count_and_byte_caps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = default_v2_ledger()
    for index in range(300):
        provenance_manifest._append_journal(
            ledger,
            (
                {
                    "event_id": f"e{index}",
                    "seq": index,
                    "manifest_generation": 1,
                    "commit_state": "committed",
                    "paths": [{"path": f"f{index}.py", "op": "write"}],
                },
            ),
        )
    journal = ledger["manifest_event_journal"]
    assert len(journal) <= provenance_manifest.JOURNAL_LIMIT  # count cap

    # byte cap: 아주 작은 cap을 주면 직렬화 크기가 cap 이하로 제한된다.
    monkeypatch.setattr(provenance_manifest, "JOURNAL_BYTE_LIMIT", 800)
    provenance_manifest._append_journal(
        ledger,
        ({"event_id": "big", "seq": 999, "manifest_generation": 1, "commit_state": "committed"},),
    )
    size = len(json.dumps(ledger["manifest_event_journal"], ensure_ascii=False))
    assert size <= 800


def test_journal_compact_preserves_pending_and_agent_log_evidence(
    tmp_path: Path,
) -> None:
    ledger = default_v2_ledger()
    _append_journal = provenance_manifest._append_journal
    _append_journal(
        ledger,
        (
            {
                "event_id": "inv:change:src",
                "seq": 1,
                "manifest_generation": 2,
                "commit_state": "committed",
                "paths": [{"path": "app.py", "op": "write"}],
            },
        ),
    )
    # pending manifest tx는 journal과 별개로 보존된다.
    ledger["manifest_pending"] = {
        "base_generation": 1,
        "target_generation": 2,
        "snapshot_before": "snapshot:before",
        "snapshot_after": "snapshot:after",
        "events": [],
    }
    # full event는 agent log에 보존된다(증거 소실 방지).
    append_agent_event(
        str(tmp_path),
        "codex",
        {
            "event_id": "inv:change:src",
            "seq": 1,
            "paths": [{"path": "app.py", "op": "write"}],
        },
    )

    assert ledger["manifest_event_journal"][0]["manifest_generation"] == 2
    assert ledger["manifest_pending"]["target_generation"] == 2
    events = load_agent_events(str(tmp_path), "codex")
    assert events is not None
    assert any(event.get("event_id") == "inv:change:src" for event in events)