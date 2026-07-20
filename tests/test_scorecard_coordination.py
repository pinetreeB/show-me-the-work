from __future__ import annotations

import argparse
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
import multiprocessing
from pathlib import Path
import time
from unittest.mock import patch

import pytest

import core.ledger as ledger_module
from core.agent_log import ledger_transaction
from core.adapter_observation import (
    CanonicalInvocation,
    record_r2_deny_after_resolution,
)
from core.destructive_guard import (
    R2_COORDINATION_REASON_MAP,
    evaluate_r2_destructive_gate,
)
from core.ledger import enqueue_coordination_event, load_ledger, record_event, save_ledger
from core.ledger_schema import LedgerSchemaError, validate_v2_ledger
from core.ledger_v1 import default_ledger
from core.ledger_v2 import default_v2_ledger
from core.scorecard import SessionIdentity
from core.scorecard_coordination import (
    CoordinationCategory,
    CoordinationEvent,
    CoordinationOutcome,
    CoordinationReason,
    CoordinationSchemaError,
    coordination_event_json,
    coordination_journal_path,
    load_coordination_journal,
    new_coordination_event,
    parse_coordination_event,
    record_peer_coordination,
    record_coordination_event,
    stable_coordination_event_id,
    try_record_coordination_event,
)
from fable_lite.scorecard import run_scorecard


def _event(
    session_id: str,
    outcome: CoordinationOutcome = CoordinationOutcome.BLOCKED,
) -> CoordinationEvent:
    category = (
        CoordinationCategory.R2_DENY
        if outcome is CoordinationOutcome.BLOCKED
        else CoordinationCategory.TURN_BOOTSTRAP
    )
    reason = (
        CoordinationReason.PEER_UNSETTLED
        if outcome is CoordinationOutcome.BLOCKED
        else CoordinationReason.COMPLETE
    )
    return new_coordination_event(
        SessionIdentity("claude_code", session_id, "claude"),
        f"turn-{session_id}",
        category,
        outcome,
        reason,
        event_id=f"event-{session_id}",
        occurred_at=datetime(2026, 7, 19, 1, 2, 3, tzinfo=UTC),
    )


def _args(root: Path, view: str, *, json_output: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        root=str(root),
        session=None,
        days=None,
        all=True,
        json=json_output,
        view=view,
    )


def _hold_ledger_lock(root: str, locked, release) -> None:
    with ledger_transaction(root):
        locked.set()
        if not release.wait(5):
            raise TimeoutError("test did not release the ledger lock")


def _missing_bootstrap_payload(root: Path, turn_id: str = "turn-outbox") -> dict:
    return {
        "project_root": str(root),
        "event": "prompt",
        "host": "codex_cli",
        "session_id": "outbox-session",
        "agent": "codex",
        "turn_id": turn_id,
        "attribution": "exact",
        "prompt": "recover provenance",
        "baseline_snapshot_id": "snapshot:unavailable",
        "current_snapshot_id": "snapshot:unavailable",
        "provenance_incomplete": True,
        "provenance_status": "incomplete",
        "provenance_status_reason": "turn_not_started",
    }


def test_coordination_schema_forbids_context_fields() -> None:
    raw = coordination_event_json(_event("privacy"))
    raw["path"] = "secret.py"

    with pytest.raises(CoordinationSchemaError, match="path"):
        parse_coordination_event(raw)


def test_coordination_schema_rejects_boolean_version() -> None:
    raw = coordination_event_json(_event("boolean-version"))
    raw["scorecard_coord_schema_version"] = True

    with pytest.raises(CoordinationSchemaError, match="schema_version"):
        parse_coordination_event(raw)


def test_coordination_writer_serializes_concurrent_lines_atomically(
    tmp_path: Path,
) -> None:
    events = [_event(f"session-{index}") for index in range(24)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda item: record_coordination_event(tmp_path, item), events)
        )

    replay = load_coordination_journal(tmp_path)
    raw_lines = coordination_journal_path(tmp_path).read_bytes().splitlines()
    assert results == [True] * 24
    assert replay.complete is True
    assert len(replay.events) == 24
    assert len(raw_lines) == 24
    assert all(isinstance(json.loads(line), dict) for line in raw_lines)


def test_turn_bootstrap_recovery_deduplicates_per_actor_and_turn(
    tmp_path: Path,
) -> None:
    actor = SessionIdentity("codex_cli", "same-session", "codex")
    references = ("invocation:first", "invocation:second")
    event_ids = [
        stable_coordination_event_id(
            tmp_path,
            actor,
            "same-turn",
            CoordinationCategory.TURN_BOOTSTRAP,
            CoordinationOutcome.RECOVERED,
            CoordinationReason.COMPLETE,
            (reference,),
        )
        for reference in references
    ]
    events = [
        new_coordination_event(
            actor,
            "same-turn",
            CoordinationCategory.TURN_BOOTSTRAP,
            CoordinationOutcome.RECOVERED,
            CoordinationReason.COMPLETE,
            evidence_refs=(reference,),
            event_id=event_id,
        )
        for reference, event_id in zip(references, event_ids, strict=True)
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda item: try_record_coordination_event(tmp_path, item), events)
        )

    replay = load_coordination_journal(tmp_path)
    r2_ids = {
        stable_coordination_event_id(
            tmp_path,
            actor,
            "same-turn",
            CoordinationCategory.R2_DENY,
            CoordinationOutcome.BLOCKED,
            CoordinationReason.PEER_UNSETTLED,
            (reference,),
        )
        for reference in references
    }
    assert event_ids[0] == event_ids[1]
    assert sorted(results) == [False, True]
    assert len(replay.events) == 1
    assert replay.events[0].outcome is CoordinationOutcome.RECOVERED
    assert len(r2_ids) == 2


def test_coordination_conflict_preserves_schema_error_across_transaction(
    tmp_path: Path,
) -> None:
    first = _event("conflict")
    conflicting = new_coordination_event(
        first.actor,
        first.actor_turn_id,
        first.category,
        first.outcome,
        first.reason_code,
        evidence_refs=("invocation:other",),
        event_id=first.event_id,
        occurred_at=first.occurred_at,
    )
    assert record_coordination_event(tmp_path, first) is True

    with pytest.raises(CoordinationSchemaError, match="event_id"):
        record_coordination_event(tmp_path, conflicting)


def test_ledger_commit_survives_crash_before_outbox_delivery(tmp_path: Path) -> None:
    with patch(
        "core.ledger._drain_coordination_outbox",
        side_effect=RuntimeError("crash before delivery"),
    ):
        committed = record_event(_missing_bootstrap_payload(tmp_path))

    pending = committed["coordination_outbox"]
    assert isinstance(pending, dict) and len(pending) == 1
    assert coordination_journal_path(tmp_path).exists() is False
    assert committed["active_turns"]

    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "scope_warning",
            "message": "retry pending observations",
        }
    )

    replay = load_coordination_journal(tmp_path)
    assert replay.complete is True
    assert len(replay.events) == 1
    assert load_ledger({"project_root": str(tmp_path)})["coordination_outbox"] == {}


def test_append_before_ack_retries_exact_content_without_duplicate(
    tmp_path: Path,
) -> None:
    with patch(
        "core.ledger._ack_coordination_outbox",
        side_effect=RuntimeError("crash before ack"),
    ):
        _ = record_event(_missing_bootstrap_payload(tmp_path))

    first_replay = load_coordination_journal(tmp_path)
    first_ledger = load_ledger({"project_root": str(tmp_path)})
    assert len(first_replay.events) == 1
    assert len(first_ledger["coordination_outbox"]) == 1

    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "scope_warning",
            "message": "deduplicate pending observation",
        }
    )

    replay = load_coordination_journal(tmp_path)
    assert replay.events == first_replay.events
    assert load_ledger({"project_root": str(tmp_path)})["coordination_outbox"] == {}


def test_ack_only_removes_the_exact_pending_content(tmp_path: Path) -> None:
    first = _event("ack-cas")
    replacement = new_coordination_event(
        first.actor,
        first.actor_turn_id,
        first.category,
        first.outcome,
        first.reason_code,
        evidence_refs=("invocation:replacement",),
        event_id=first.event_id,
        occurred_at=first.occurred_at,
    )
    first_raw = coordination_event_json(first)
    replacement_raw = coordination_event_json(replacement)
    ledger = default_v2_ledger()
    ledger["coordination_outbox"] = {first.event_id: replacement_raw}
    assert save_ledger({"project_root": str(tmp_path)}, ledger) is True

    ledger_module._ack_coordination_outbox(
        str(tmp_path),
        {first.event_id: first_raw},
        degraded=False,
    )

    loaded = load_ledger({"project_root": str(tmp_path)})
    assert loaded["coordination_outbox"] == {first.event_id: replacement_raw}
    assert loaded["coordination_delivered"] == {}


def test_duplicate_enqueue_preserves_the_first_exact_event_content(
    tmp_path: Path,
) -> None:
    with patch(
        "core.ledger._drain_coordination_outbox",
        side_effect=RuntimeError("hold first winner"),
    ):
        first = record_event(_missing_bootstrap_payload(tmp_path))
        first_raw = json.dumps(
            first["coordination_outbox"],
            ensure_ascii=False,
            sort_keys=True,
        )
        second = record_event(_missing_bootstrap_payload(tmp_path))

    assert len(second["coordination_outbox"]) == 1
    assert (
        json.dumps(
            second["coordination_outbox"],
            ensure_ascii=False,
            sort_keys=True,
        )
        == first_raw
    )
    assert second["coordination_degraded"] is False


def test_delivered_id_prevents_later_stable_event_republication(
    tmp_path: Path,
) -> None:
    _ = record_event(_missing_bootstrap_payload(tmp_path))
    first_replay = load_coordination_journal(tmp_path)

    _ = record_event(_missing_bootstrap_payload(tmp_path))

    ledger = load_ledger({"project_root": str(tmp_path)})
    replay = load_coordination_journal(tmp_path)
    assert replay.events == first_replay.events
    assert len(replay.events) == 1
    assert ledger["coordination_outbox"] == {}
    assert len(ledger["coordination_delivered"]) == 1
    assert ledger["coordination_degraded"] is False


def test_recovered_bootstrap_rebuilds_the_first_canonical_content_after_eviction(
    tmp_path: Path,
) -> None:
    _ = record_event(_missing_bootstrap_payload(tmp_path))
    recovered = _missing_bootstrap_payload(tmp_path) | {
        "event": "turn_bootstrap_recovered",
        "invocation_id": "first-recovery",
        "baseline_snapshot_id": "snapshot:ready",
        "current_snapshot_id": "snapshot:ready",
        "baseline_status": "ready",
        "provenance_incomplete": False,
        "provenance_status": "complete",
        "provenance_status_reason": "",
        "turn_bootstrap_recovered": True,
    }
    _ = record_event(recovered)
    replay = load_coordination_journal(tmp_path)
    recovered_event = next(
        event for event in replay.events if event.outcome is CoordinationOutcome.RECOVERED
    )
    ledger = load_ledger({"project_root": str(tmp_path)})
    del ledger["coordination_delivered"][recovered_event.event_id]
    ledger["coordination_delivered_order"].remove(recovered_event.event_id)
    assert save_ledger({"project_root": str(tmp_path)}, ledger) is True

    _ = record_event(recovered | {"invocation_id": "second-recovery"})

    loaded = load_ledger({"project_root": str(tmp_path)})
    recovered_events = [
        event
        for event in load_coordination_journal(tmp_path).events
        if event.outcome is CoordinationOutcome.RECOVERED
    ]
    assert recovered_events == [recovered_event]
    assert recovered_event.evidence_refs == ("invocation:first-recovery",)
    assert loaded["coordination_outbox"] == {}
    assert loaded["coordination_degraded"] is False


def test_recovery_audit_requires_an_applied_missing_to_ready_transition(
    tmp_path: Path,
) -> None:
    roots = {
        name: tmp_path / name
        for name in ("absent", "stale", "closed", "degraded")
    }
    recovery = _missing_bootstrap_payload(tmp_path) | {
        "event": "turn_bootstrap_recovered",
        "invocation_id": "recovery",
        "baseline_snapshot_id": "snapshot:ready",
        "current_snapshot_id": "snapshot:ready",
        "baseline_status": "ready",
        "provenance_incomplete": False,
        "provenance_status": "complete",
        "provenance_status_reason": "",
        "turn_bootstrap_recovered": True,
    }

    _ = record_event(recovery | {"project_root": str(roots["absent"])})

    _ = record_event(_missing_bootstrap_payload(roots["stale"], "live-turn"))
    _ = record_event(
        recovery
        | {"project_root": str(roots["stale"]), "turn_id": "stale-turn"}
    )

    _ = record_event(_missing_bootstrap_payload(roots["closed"]))
    _ = record_event(
        _missing_bootstrap_payload(roots["closed"]) | {"event": "turn_finished"}
    )
    _ = record_event(recovery | {"project_root": str(roots["closed"])})

    _ = record_event(_missing_bootstrap_payload(roots["degraded"]))
    _ = record_event(
        _missing_bootstrap_payload(roots["degraded"])
        | {
            "event": "scope_warning",
            "baseline_status": "degraded",
            "provenance_status_reason": "baseline_state_mismatch",
        }
    )
    _ = record_event(recovery | {"project_root": str(roots["degraded"])})

    for root in roots.values():
        assert all(
            event.outcome is not CoordinationOutcome.RECOVERED
            for event in load_coordination_journal(root).events
        )
    stale_turns = load_ledger({"project_root": str(roots["stale"])})[
        "active_turns"
    ]
    assert next(iter(stale_turns.values()))["turn_id"] == "live-turn"
    degraded_turn = next(
        iter(
            load_ledger({"project_root": str(roots["degraded"])})[
                "active_turns"
            ].values()
        )
    )
    assert degraded_turn["baseline_status"] == "degraded"


def test_invalid_recovery_cannot_poison_canonical_recovery_metadata(
    tmp_path: Path,
) -> None:
    _ = record_event(_missing_bootstrap_payload(tmp_path))
    _ = record_event(
        _missing_bootstrap_payload(tmp_path)
        | {
            "event": "turn_bootstrap_recovered",
            "invocation_id": "bad",
            "timestamp": "2026-07-19T01:02:03+00:00",
        }
    )
    recovered = _missing_bootstrap_payload(tmp_path) | {
        "event": "turn_bootstrap_recovered",
        "invocation_id": "good",
        "timestamp": "2026-07-19T01:02:04+00:00",
        "baseline_snapshot_id": "snapshot:ready",
        "current_snapshot_id": "snapshot:ready",
        "baseline_status": "ready",
        "provenance_incomplete": False,
        "provenance_status": "complete",
        "provenance_status_reason": "",
        "turn_bootstrap_recovered": True,
    }

    _ = record_event(recovered)

    recovery = next(
        event
        for event in load_coordination_journal(tmp_path).events
        if event.outcome is CoordinationOutcome.RECOVERED
    )
    assert recovery.evidence_refs == ("invocation:good",)
    assert recovery.occurred_at == datetime(2026, 7, 19, 1, 2, 4, tzinfo=UTC)
    turn = next(
        iter(load_ledger({"project_root": str(tmp_path)})["active_turns"].values())
    )
    assert turn["bootstrap_recovery_evidence_refs"] == ["invocation:good"]
    assert turn["bootstrap_recovered_at"] == "2026-07-19T01:02:04+00:00"


def test_transient_coordination_failure_stays_pending_and_gate_state_commits(
    tmp_path: Path,
) -> None:
    with patch(
        "core.scorecard_coordination._append_coordination_event",
        side_effect=PermissionError("read-only journal"),
    ):
        committed = record_event(_missing_bootstrap_payload(tmp_path))

    assert committed["active_turns"]
    assert len(committed["coordination_outbox"]) == 1
    persisted = load_ledger({"project_root": str(tmp_path)})
    assert persisted["coordination_degraded"] is True
    assert coordination_journal_path(tmp_path).exists() is False


def test_peer_coordination_accepts_exact_retries_and_rejects_conflicts(
    tmp_path: Path,
) -> None:
    first = _event("durable-peer")
    conflicting = new_coordination_event(
        first.actor,
        first.actor_turn_id,
        first.category,
        first.outcome,
        first.reason_code,
        evidence_refs=("invocation:different",),
        event_id=first.event_id,
        occurred_at=first.occurred_at,
    )
    retry_with_new_clock = new_coordination_event(
        first.actor,
        first.actor_turn_id,
        first.category,
        first.outcome,
        first.reason_code,
        event_id=first.event_id,
        occurred_at=datetime(2026, 7, 19, 1, 2, 4, tzinfo=UTC),
    )
    with patch(
        "core.ledger._drain_coordination_outbox",
        side_effect=PermissionError("journal unavailable"),
    ):
        assert record_peer_coordination(tmp_path, first) is True
        first_pending = load_ledger({"project_root": str(tmp_path)})[
            "coordination_outbox"
        ]
        assert record_peer_coordination(tmp_path, first) is True
        assert record_peer_coordination(tmp_path, retry_with_new_clock) is False
        assert record_peer_coordination(tmp_path, conflicting) is False

    ledger = load_ledger({"project_root": str(tmp_path)})
    assert ledger["coordination_outbox"] == first_pending
    assert ledger["coordination_degraded"] is True

    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "scope_warning",
            "message": "deliver peer audit",
        }
    )
    assert record_peer_coordination(tmp_path, first) is True
    delivered = load_ledger({"project_root": str(tmp_path)})
    assert delivered["coordination_outbox"] == {}
    assert len(delivered["coordination_delivered"]) == 1
    assert len(load_coordination_journal(tmp_path).events) == 1


def test_legacy_peer_coordination_duplicate_is_still_durably_accepted(
    tmp_path: Path,
) -> None:
    assert save_ledger({"project_root": str(tmp_path)}, default_ledger()) is True
    event = _event("legacy-peer")

    assert record_peer_coordination(tmp_path, event) is True
    assert record_peer_coordination(tmp_path, event) is True

    assert len(load_coordination_journal(tmp_path).events) == 1
    assert load_ledger({"project_root": str(tmp_path)}).get("schema_version") != 2


def test_conflicting_pending_event_stays_pending_and_reports_degraded(
    tmp_path: Path,
) -> None:
    journal_event = _event("poison")
    assert record_coordination_event(tmp_path, journal_event) is True
    conflicting = new_coordination_event(
        journal_event.actor,
        journal_event.actor_turn_id,
        journal_event.category,
        journal_event.outcome,
        journal_event.reason_code,
        evidence_refs=("invocation:conflict",),
        event_id=journal_event.event_id,
        occurred_at=journal_event.occurred_at,
    )
    ledger = default_v2_ledger()
    ledger["coordination_outbox"] = {
        conflicting.event_id: coordination_event_json(conflicting)
    }
    assert save_ledger({"project_root": str(tmp_path)}, ledger) is True

    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "scope_warning",
            "message": "drain poison",
        }
    )

    loaded = load_ledger({"project_root": str(tmp_path)})
    assert len(loaded["coordination_outbox"]) == 1
    assert loaded["coordination_degraded"] is True
    assert load_coordination_journal(tmp_path).events == (journal_event,)


def test_drain_cursor_prevents_conflicts_from_starving_later_events(
    tmp_path: Path,
) -> None:
    journal_events = [_event(f"a-conflict-{index:02d}") for index in range(16)]
    for event in journal_events:
        assert record_coordination_event(tmp_path, event) is True
    conflicting = [
        new_coordination_event(
            event.actor,
            event.actor_turn_id,
            event.category,
            event.outcome,
            event.reason_code,
            evidence_refs=("invocation:conflict",),
            event_id=event.event_id,
            occurred_at=event.occurred_at,
        )
        for event in journal_events
    ]
    healthy = _event("z-healthy")
    ledger = default_v2_ledger()
    ledger["coordination_outbox"] = {
        event.event_id: coordination_event_json(event)
        for event in (*conflicting, healthy)
    }
    assert save_ledger({"project_root": str(tmp_path)}, ledger) is True

    for attempt in range(2):
        _ = record_event(
            {
                "project_root": str(tmp_path),
                "event": "scope_warning",
                "message": f"rotate drain batch {attempt}",
            }
        )

    loaded = load_ledger({"project_root": str(tmp_path)})
    replay = load_coordination_journal(tmp_path)
    assert len(loaded["coordination_outbox"]) == 16
    assert healthy.event_id not in loaded["coordination_outbox"]
    assert healthy.event_id in loaded["coordination_delivered"]
    assert any(event.event_id == healthy.event_id for event in replay.events)


def test_transient_drain_failure_stops_the_batch_and_advances_one_slot(
    tmp_path: Path,
) -> None:
    ledger = default_v2_ledger()
    ledger["coordination_outbox"] = {
        event.event_id: coordination_event_json(event)
        for event in (_event(f"timeout-{index}") for index in range(16))
    }
    assert save_ledger({"project_root": str(tmp_path)}, ledger) is True

    with patch(
        "core.scorecard_coordination.record_coordination_event_for_delivery",
        side_effect=TimeoutError("busy ledger"),
    ) as writer:
        _ = record_event(
            {
                "project_root": str(tmp_path),
                "event": "scope_warning",
                "message": "bounded drain failure",
            }
        )

    loaded = load_ledger({"project_root": str(tmp_path)})
    assert writer.call_count == 1
    assert len(loaded["coordination_outbox"]) == 16
    assert loaded["coordination_drain_cursor"] == 1
    assert loaded["coordination_degraded"] is True


def test_coordination_outbox_overflow_is_bounded_and_degraded(tmp_path: Path) -> None:
    ledger = default_v2_ledger()
    ledger["coordination_outbox"] = {
        event.event_id: coordination_event_json(event)
        for event in (_event(f"queued-{index}") for index in range(256))
    }
    assert save_ledger({"project_root": str(tmp_path)}, ledger) is True

    with patch(
        "core.ledger._drain_coordination_outbox",
        side_effect=RuntimeError("preserve full queue"),
    ):
        committed = record_event(
            _missing_bootstrap_payload(tmp_path, "overflow-turn")
        )

    assert len(committed["coordination_outbox"]) == 256
    assert committed["coordination_degraded"] is True
    assert committed["active_turns"]


def test_coordination_backlog_and_degraded_flag_do_not_change_r2_decision(
    tmp_path: Path,
) -> None:
    control_root = tmp_path / "control"
    backlog_root = tmp_path / "backlog"
    control = default_v2_ledger()
    backlog = default_v2_ledger()
    pending = _event("gate-independent")
    backlog["coordination_outbox"] = {
        pending.event_id: coordination_event_json(pending)
    }
    backlog["coordination_degraded"] = True
    assert save_ledger({"project_root": str(control_root)}, control) is True
    assert save_ledger({"project_root": str(backlog_root)}, backlog) is True
    base_payload = {
        "tool_name": "Bash",
        "command": "rm peer.py",
        "host": "claude_code",
        "session_id": "caller",
        "agent": "claude",
    }

    control_decision = evaluate_r2_destructive_gate(
        base_payload | {"project_root": str(control_root)}
    )
    backlog_decision = evaluate_r2_destructive_gate(
        base_payload | {"project_root": str(backlog_root)}
    )

    assert backlog_decision == control_decision


def test_malformed_persisted_outbox_is_sanitized_fail_open(tmp_path: Path) -> None:
    ledger = default_v2_ledger()
    malformed = coordination_event_json(_event("malformed-outbox"))
    del malformed["reason_code"]
    valid = _event("valid-outbox")
    ledger["coordination_outbox"] = {
        "event-malformed-outbox": malformed,
        valid.event_id: coordination_event_json(valid),
    }
    path = tmp_path / ".fable-lite" / "ledger.json"
    path.parent.mkdir(parents=True)
    _ = path.write_text(json.dumps(ledger), encoding="utf-8")

    loaded = load_ledger({"project_root": str(tmp_path)})

    assert loaded["coordination_outbox"] == {
        valid.event_id: coordination_event_json(valid)
    }
    assert loaded["coordination_degraded"] is True
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "scope_warning",
            "message": "persist sanitation",
        }
    )
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["coordination_outbox"] == {}
    assert valid.event_id in persisted["coordination_delivered"]
    assert persisted["coordination_degraded"] is True


def test_malformed_enqueue_is_rejected_and_persists_degraded(tmp_path: Path) -> None:
    malformed = coordination_event_json(_event("malformed-enqueue"))
    del malformed["reason_code"]

    accepted = enqueue_coordination_event(str(tmp_path), malformed)

    assert accepted is False
    path = tmp_path / ".fable-lite" / "ledger.json"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert validate_v2_ledger(persisted) is persisted
    assert persisted["coordination_outbox"] == {}
    assert persisted["coordination_delivered"] == {}
    assert persisted["coordination_degraded"] is True
    assert coordination_journal_path(tmp_path).exists() is False

    valid = _event("valid-after-malformed")
    assert enqueue_coordination_event(
        str(tmp_path), coordination_event_json(valid)
    ) is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bootstrap_recovered_at", []),
        ("bootstrap_recovery_evidence_refs", [""]),
    ],
)
def test_malformed_bootstrap_coordination_metadata_is_sanitized_fail_open(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    _ = record_event(_missing_bootstrap_payload(tmp_path))
    path = tmp_path / ".fable-lite" / "ledger.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    turn = next(iter(raw["active_turns"].values()))
    turn[field] = value
    _ = path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_ledger({"project_root": str(tmp_path)})

    loaded_turn = next(iter(loaded["active_turns"].values()))
    assert field not in loaded_turn
    assert loaded["coordination_degraded"] is True
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "scope_warning",
            "message": "persist bootstrap coordination sanitation",
        }
    )
    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted_turn = next(iter(persisted["active_turns"].values()))
    assert field not in persisted_turn
    assert persisted["coordination_degraded"] is True


def test_coordination_outbox_schema_rejects_more_than_256_entries() -> None:
    ledger = default_v2_ledger()
    ledger["coordination_outbox"] = {
        event.event_id: coordination_event_json(event)
        for event in (_event(f"schema-{index}") for index in range(257))
    }

    with pytest.raises(LedgerSchemaError, match="at most 256"):
        _ = validate_v2_ledger(ledger)


def test_coordination_outbox_schema_rejects_noncanonical_event_shapes() -> None:
    original = coordination_event_json(_event("canonical-shape"))
    cases: list[tuple[str, dict]] = []
    missing = deepcopy(original)
    del missing["reason_code"]
    cases.append((str(original["event_id"]), missing))
    extra = deepcopy(original)
    extra["path"] = "secret.py"
    cases.append((str(original["event_id"]), extra))
    actor_extra = deepcopy(original)
    assert isinstance(actor_extra["actor"], dict)
    actor_extra["actor"]["role"] = "owner"
    cases.append((str(original["event_id"]), actor_extra))
    invalid_category = deepcopy(original)
    invalid_category["category"] = "unknown"
    cases.append((str(original["event_id"]), invalid_category))
    too_much_evidence = deepcopy(original)
    too_much_evidence["evidence_refs"] = [f"ref:{index}" for index in range(33)]
    cases.append((str(original["event_id"]), too_much_evidence))
    non_utc = deepcopy(original)
    non_utc["occurred_at"] = "2026-07-19T10:02:03+09:00"
    cases.append((str(original["event_id"]), non_utc))
    cases.append(("different-event-id", deepcopy(original)))

    for event_id, raw in cases:
        ledger = default_v2_ledger()
        ledger["coordination_outbox"] = {event_id: raw}
        with pytest.raises(LedgerSchemaError):
            _ = validate_v2_ledger(ledger)

    invalid_health = default_v2_ledger()
    invalid_health["coordination_degraded"] = "yes"
    with pytest.raises(LedgerSchemaError, match="coordination_degraded"):
        _ = validate_v2_ledger(invalid_health)


def test_legacy_v2_ledger_may_omit_all_coordination_fields() -> None:
    ledger = default_v2_ledger()
    for field in (
        "coordination_outbox",
        "coordination_degraded",
        "coordination_drain_cursor",
        "coordination_delivered",
        "coordination_delivered_order",
    ):
        del ledger[field]

    assert validate_v2_ledger(ledger) is ledger


def test_coordination_delivered_schema_is_bounded_and_disjoint() -> None:
    ledger = default_v2_ledger()
    delivered = {
        event.event_id: coordination_event_json(event)
        for event in (_event(f"delivered-{index}") for index in range(257))
    }
    ledger["coordination_delivered"] = delivered
    with pytest.raises(LedgerSchemaError, match="at most 256"):
        _ = validate_v2_ledger(ledger)

    one_id, one_raw = next(iter(delivered.items()))
    ledger["coordination_delivered"] = {one_id: one_raw}
    ledger["coordination_delivered_order"] = [one_id]
    ledger["coordination_outbox"] = {one_id: one_raw}
    with pytest.raises(LedgerSchemaError, match="must not overlap"):
        _ = validate_v2_ledger(ledger)


def test_delivered_receipts_evict_the_oldest_acknowledgement(tmp_path: Path) -> None:
    events = [_event(f"receipt-{index:03d}") for index in range(257)]
    ledger = default_v2_ledger()
    ledger["coordination_delivered"] = {
        event.event_id: coordination_event_json(event) for event in events[:256]
    }
    ledger["coordination_delivered_order"] = [
        event.event_id for event in events[:256]
    ]
    newest = events[-1]
    newest_raw = coordination_event_json(newest)
    ledger["coordination_outbox"] = {newest.event_id: newest_raw}
    assert save_ledger({"project_root": str(tmp_path)}, ledger) is True

    ledger_module._ack_coordination_outbox(
        str(tmp_path),
        {newest.event_id: newest_raw},
        degraded=False,
    )

    loaded = load_ledger({"project_root": str(tmp_path)})
    oldest_id = events[0].event_id
    assert len(loaded["coordination_delivered"]) == 256
    assert oldest_id not in loaded["coordination_delivered"]
    assert newest.event_id in loaded["coordination_delivered"]
    assert loaded["coordination_delivered_order"][0] == events[1].event_id
    assert loaded["coordination_delivered_order"][-1] == newest.event_id


def test_coordination_replay_keeps_valid_events_and_marks_malformed_incomplete(
    tmp_path: Path,
) -> None:
    first = coordination_event_json(_event("first"))
    second = coordination_event_json(_event("second"))
    path = coordination_journal_path(tmp_path)
    path.parent.mkdir(parents=True)
    _ = path.write_text(
        json.dumps(first) + "\n{broken\n" + json.dumps(second) + "\n",
        encoding="utf-8",
    )

    replay = load_coordination_journal(tmp_path)

    assert replay.complete is False
    assert [item.actor.session_id for item in replay.events] == ["first", "second"]


def test_coordination_replay_quarantines_valid_json_with_invalid_schema(
    tmp_path: Path,
) -> None:
    first = coordination_event_json(_event("first-schema"))
    malformed = coordination_event_json(_event("missing-reason"))
    del malformed["reason_code"]
    second = coordination_event_json(_event("second-schema"))
    path = coordination_journal_path(tmp_path)
    path.parent.mkdir(parents=True)
    _ = path.write_text(
        "\n".join(json.dumps(item) for item in (first, malformed, second)) + "\n",
        encoding="utf-8",
    )

    replay = load_coordination_journal(tmp_path)

    assert replay.complete is False
    assert [item.actor.session_id for item in replay.events] == [
        "first-schema",
        "second-schema",
    ]


def test_r2_static_block_points_map_to_closed_reasons() -> None:
    expected = {
        "ledger_degraded": CoordinationReason.ATTRIBUTION_DEGRADED,
        "attribution_health_unavailable": CoordinationReason.ATTRIBUTION_DEGRADED,
        "attribution_degraded_or_capacity_exceeded": CoordinationReason.ATTRIBUTION_DEGRADED,
        "canonicalization_unavailable": CoordinationReason.UNRESOLVABLE_TARGET,
        "state_dir_protected": CoordinationReason.STATE_DIR_PROTECTED,
        "attribution_lookup_unavailable": CoordinationReason.ATTRIBUTION_DEGRADED,
        "peer_unsettled_revision": CoordinationReason.PEER_UNSETTLED,
        "peer_open_invocation_candidate": CoordinationReason.PEER_UNSETTLED,
        "parse_unable_dynamic_command": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
        "parse_unable_dynamic_expression": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
        "parse_unable_missing_path_flag": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
        "parse_unable_missing_target": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
        "parse_unable_missing_value": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
        "parse_unable_obfuscated": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
        "parse_unable_pathspec_from_file": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
        "parse_unable_pipeline": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
        "parse_unable_subcommand": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
        "parse_unable_target": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
        "parse_unable_wrapped": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    }

    assert R2_COORDINATION_REASON_MAP == expected
    assert len(R2_COORDINATION_REASON_MAP) == 19
    assert set(R2_COORDINATION_REASON_MAP.values()) == {
        CoordinationReason.ATTRIBUTION_DEGRADED,
        CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
        CoordinationReason.PEER_UNSETTLED,
        CoordinationReason.STATE_DIR_PROTECTED,
        CoordinationReason.UNRESOLVABLE_TARGET,
    }


def test_r2_deny_audit_fails_fast_while_ledger_lock_is_held(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    locked = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_ledger_lock,
        args=(str(tmp_path), locked, release),
    )
    holder.start()
    assert locked.wait(5)
    invocation = CanonicalInvocation(
        "claude_code",
        "claude",
        "contended-session",
        "contended-turn",
        "contended-invocation",
        "pre_tool",
        "shell",
        (),
        "rm",
        False,
        "",
    )
    payload = {
        "project_root": str(tmp_path),
        "tool_name": "Bash",
        "command": "rm",
        "host": invocation.host,
        "session_id": invocation.session_id,
        "agent": invocation.agent,
    }
    try:
        with patch("core.agent_log._lock_wait_seconds", return_value=1.1):
            started = time.perf_counter()
            decision = evaluate_r2_destructive_gate(payload)
            recorded = record_r2_deny_after_resolution(
                tmp_path,
                invocation,
                str(decision["coordination_reason_code"]),
            )
            elapsed = time.perf_counter() - started
    finally:
        release.set()
        holder.join(timeout=5)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)

    assert holder.is_alive() is False
    assert holder.exitcode == 0
    assert decision["decision"] == "block"
    assert recorded is False
    assert elapsed < 1.0

    reason = str(decision["coordination_reason_code"])
    assert record_r2_deny_after_resolution(tmp_path, invocation, reason) is True
    first_pending = load_ledger({"project_root": str(tmp_path)})[
        "coordination_outbox"
    ]
    assert isinstance(first_pending, dict) and len(first_pending) == 1
    assert record_r2_deny_after_resolution(tmp_path, invocation, reason) is True
    assert load_ledger({"project_root": str(tmp_path)})[
        "coordination_outbox"
    ] == first_pending

    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "scope_warning",
            "message": "deliver retried R2 audit",
        }
    )
    assert len(load_coordination_journal(tmp_path).events) == 1


def test_r2_deny_response_path_never_scans_the_coordination_journal(
    tmp_path: Path,
) -> None:
    invocation = CanonicalInvocation(
        "claude_code",
        "claude",
        "bounded-session",
        "bounded-turn",
        "bounded-invocation",
        "pre_tool",
        "shell",
        (),
        "rm",
        False,
        "",
    )

    with patch(
        "core.scorecard_coordination.load_coordination_journal",
        side_effect=AssertionError("R2 response path must not scan the journal"),
    ):
        started = time.perf_counter()
        recorded = record_r2_deny_after_resolution(
            tmp_path,
            invocation,
            CoordinationReason.PEER_UNSETTLED.value,
        )
        elapsed = time.perf_counter() - started

    assert recorded is True
    assert elapsed < 1.0
    pending = load_ledger({"project_root": str(tmp_path)})["coordination_outbox"]
    assert isinstance(pending, dict) and len(pending) == 1


def test_r2_deny_response_does_not_wait_to_retry_lock_release(
    tmp_path: Path,
) -> None:
    invocation = CanonicalInvocation(
        "claude_code",
        "claude",
        "release-session",
        "release-turn",
        "release-invocation",
        "pre_tool",
        "shell",
        (),
        "rm",
        False,
        "",
    )
    lock_path = tmp_path / ".fable-lite" / "ledger.lock"
    try:
        with patch("core.agent_log._unlink_matching_record", return_value=False):
            started = time.perf_counter()
            recorded = record_r2_deny_after_resolution(
                tmp_path,
                invocation,
                CoordinationReason.PEER_UNSETTLED.value,
            )
            elapsed = time.perf_counter() - started
    finally:
        lock_path.unlink(missing_ok=True)

    assert recorded is True
    assert elapsed < 1.0


def test_r2_deny_legacy_ledger_skips_unbounded_journal_fallback(
    tmp_path: Path,
) -> None:
    assert save_ledger({"project_root": str(tmp_path)}, default_ledger()) is True
    invocation = CanonicalInvocation(
        "claude_code",
        "claude",
        "legacy-session",
        "legacy-turn",
        "legacy-invocation",
        "pre_tool",
        "shell",
        (),
        "rm",
        False,
        "",
    )

    with patch(
        "core.scorecard_coordination.load_coordination_journal",
        side_effect=AssertionError("legacy R2 response must not scan the journal"),
    ):
        recorded = record_r2_deny_after_resolution(
            tmp_path,
            invocation,
            CoordinationReason.PEER_UNSETTLED.value,
        )

    assert recorded is False
    assert coordination_journal_path(tmp_path).exists() is False


def test_r2_deny_records_only_the_resolved_active_identity(tmp_path: Path) -> None:
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "host": "claude_code",
            "session_id": "resolved-session",
            "agent": "claude",
            "turn_id": "resolved-turn",
            "attribution": "exact",
            "prompt": "work",
            "baseline_snapshot_id": "snapshot:base",
            "current_snapshot_id": "snapshot:base",
            "provenance_incomplete": False,
            "provenance_status": "complete",
            "provenance_status_reason": "",
        }
    )
    raw = CanonicalInvocation(
        "claude_code",
        "claude",
        "raw-session",
        "raw-turn",
        "r2-attempt",
        "pre_tool",
        "shell",
        (),
        "rm peer.py",
        False,
        "",
        True,
        True,
    )

    recorded = record_r2_deny_after_resolution(
        tmp_path,
        raw,
        CoordinationReason.PEER_UNSETTLED.value,
    )

    assert recorded is True
    pending = load_ledger({"project_root": str(tmp_path)})["coordination_outbox"]
    assert isinstance(pending, dict) and len(pending) == 1
    pending_event = parse_coordination_event(next(iter(pending.values())))
    assert pending_event.actor.session_id == "resolved-session"
    assert pending_event.actor_turn_id == "resolved-turn"
    assert pending_event.actor.session_id != raw.session_id

    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "scope_warning",
            "message": "deliver resolved R2 audit",
        }
    )
    replay = load_coordination_journal(tmp_path)
    assert len(replay.events) == 1
    assert replay.events[0].actor.session_id == "resolved-session"
    assert replay.events[0].actor_turn_id == "resolved-turn"
    assert replay.events[0].actor.session_id != raw.session_id


def test_r2_coordination_write_failure_cannot_change_block_decision(
    tmp_path: Path,
) -> None:
    decision = evaluate_r2_destructive_gate(
        {
            "project_root": str(tmp_path),
            "tool_name": "Bash",
            "command": "rm",
            "host": "claude_code",
            "session_id": "s1",
            "agent": "claude",
        }
    )
    invocation = CanonicalInvocation(
        "claude_code",
        "claude",
        "s1",
        "turn-1",
        "r2-failed-write",
        "pre_tool",
        "shell",
        (),
        "rm",
        False,
        "",
    )

    with patch(
        "core.scorecard_coordination._append_coordination_event",
        side_effect=PermissionError("read-only journal"),
    ):
        recorded = record_r2_deny_after_resolution(
            tmp_path,
            invocation,
            str(decision["coordination_reason_code"]),
        )
        _ = record_event(
            {
                "project_root": str(tmp_path),
                "event": "scope_warning",
                "message": "attempt R2 audit delivery",
            }
        )

    assert decision["decision"] == "block"
    assert recorded is True
    assert coordination_journal_path(tmp_path).exists() is False
    ledger = load_ledger({"project_root": str(tmp_path)})
    assert len(ledger["coordination_outbox"]) == 1
    assert ledger["coordination_degraded"] is True


def test_r2_retry_reuses_the_first_exact_canonical_event(tmp_path: Path) -> None:
    invocation = CanonicalInvocation(
        "claude_code",
        "claude",
        "retry-session",
        "retry-turn",
        "retry-invocation",
        "pre_tool",
        "shell",
        (),
        "rm peer.py",
        False,
        "",
    )

    first = record_r2_deny_after_resolution(
        tmp_path,
        invocation,
        CoordinationReason.PEER_UNSETTLED.value,
    )
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "scope_warning",
            "message": "deliver first R2 audit",
        }
    )
    first_event = load_coordination_journal(tmp_path).events[0]
    receiptless = load_ledger({"project_root": str(tmp_path)})
    del receiptless["coordination_delivered"][first_event.event_id]
    receiptless["coordination_delivered_order"].remove(first_event.event_id)
    assert save_ledger({"project_root": str(tmp_path)}, receiptless) is True
    second = record_r2_deny_after_resolution(
        tmp_path,
        invocation,
        CoordinationReason.PEER_UNSETTLED.value,
    )
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "scope_warning",
            "message": "reconcile retried R2 audit",
        }
    )

    ledger = load_ledger({"project_root": str(tmp_path)})
    assert first is True and second is True
    assert len(load_coordination_journal(tmp_path).events) == 1
    assert ledger["coordination_outbox"] == {}
    assert ledger["coordination_delivered"][first_event.event_id] == (
        coordination_event_json(first_event)
    )
    assert ledger["coordination_degraded"] is False


def test_scorecard_cli_default_sessions_is_byte_identical_to_explicit_view(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    default_args = _args(tmp_path, "sessions")
    delattr(default_args, "view")
    assert run_scorecard(default_args) == 0
    default_output = capsys.readouterr().out

    assert run_scorecard(_args(tmp_path, "sessions")) == 0
    explicit_output = capsys.readouterr().out

    assert explicit_output == default_output


def test_scorecard_cli_agents_and_coordination_views_render_journal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert record_coordination_event(tmp_path, _event("cli")) is True

    assert run_scorecard(_args(tmp_path, "agents")) == 0
    agents = json.loads(capsys.readouterr().out)
    assert agents["view"] == "agents"
    assert agents["agents"][0]["r2_denies"] == 1

    assert run_scorecard(_args(tmp_path, "coordination")) == 0
    coordination = json.loads(capsys.readouterr().out)
    assert coordination["view"] == "coordination"
    assert coordination["coordination"][0]["reason_code"] == "peer_unsettled"


def test_scorecard_cli_coordination_malformed_is_not_reported_as_complete_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = coordination_journal_path(tmp_path)
    path.parent.mkdir(parents=True)
    _ = path.write_text("{malformed\n", encoding="utf-8")

    assert run_scorecard(_args(tmp_path, "coordination")) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["complete"] is False
    assert result["coordination"] == []
