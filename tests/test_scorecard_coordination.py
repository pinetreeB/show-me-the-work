from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.adapter_observation import (
    CanonicalInvocation,
    record_r2_deny_after_resolution,
)
from core.destructive_guard import (
    R2_COORDINATION_REASON_MAP,
    evaluate_r2_destructive_gate,
)
from core.ledger import record_event
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


def test_coordination_schema_forbids_context_fields() -> None:
    raw = coordination_event_json(_event("privacy"))
    raw["path"] = "secret.py"

    with pytest.raises(CoordinationSchemaError, match="path"):
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


def test_r2_eight_static_block_points_map_to_four_closed_reasons() -> None:
    expected = {
        "ledger_degraded": CoordinationReason.ATTRIBUTION_DEGRADED,
        "attribution_health_unavailable": CoordinationReason.ATTRIBUTION_DEGRADED,
        "attribution_degraded_or_capacity_exceeded": CoordinationReason.ATTRIBUTION_DEGRADED,
        "canonicalization_unavailable": CoordinationReason.UNRESOLVABLE_TARGET,
        "state_dir_protected": CoordinationReason.STATE_DIR_PROTECTED,
        "attribution_lookup_unavailable": CoordinationReason.ATTRIBUTION_DEGRADED,
        "peer_unsettled_revision": CoordinationReason.PEER_UNSETTLED,
        "peer_open_invocation_candidate": CoordinationReason.PEER_UNSETTLED,
    }

    assert R2_COORDINATION_REASON_MAP == expected
    assert len(R2_COORDINATION_REASON_MAP) == 8
    assert set(R2_COORDINATION_REASON_MAP.values()) == {
        CoordinationReason.ATTRIBUTION_DEGRADED,
        CoordinationReason.PEER_UNSETTLED,
        CoordinationReason.STATE_DIR_PROTECTED,
        CoordinationReason.UNRESOLVABLE_TARGET,
    }


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
    )

    recorded = record_r2_deny_after_resolution(
        tmp_path,
        raw,
        CoordinationReason.PEER_UNSETTLED.value,
    )
    replay = load_coordination_journal(tmp_path)

    assert recorded is True
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

    assert decision["decision"] == "block"
    assert recorded is False
    assert coordination_journal_path(tmp_path).exists() is False


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
