from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from core.ledger_schema import JsonObject, JsonValue
from core.scorecard import (
    Attribution,
    GateAction,
    GateTransition,
    ScorecardAggregate,
    ScorecardSchemaError,
    SessionIdentity,
    aggregate_transitions,
    empty_aggregate,
)
from core.scorecard_coordination import (
    CoordinationCategory,
    CoordinationEvent,
    CoordinationOutcome,
    load_coordination_journal,
)
from core.scorecard_store import load_scorecard_journal

from .scorecard_observations import load_observations


DEFAULT_DAYS = 7


def add_scorecard_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "scorecard", help="세션별 차단·회복·cap 통계를 표시합니다."
    )
    parser.add_argument("--root", default=".")
    view = parser.add_mutually_exclusive_group()
    view.add_argument("--session")
    view.add_argument("--days", type=int)
    view.add_argument("--all", action="store_true")
    parser.add_argument(
        "--view",
        choices=("sessions", "agents", "coordination"),
        default="sessions",
    )
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=run_scorecard)


def run_scorecard(args: argparse.Namespace) -> int:
    root = Path(str(args.root)).resolve()
    selected_view = str(getattr(args, "view", "sessions"))
    if selected_view == "agents":
        return _run_agents_view(root, args)
    if selected_view == "coordination":
        return _run_coordination_view(root, args)
    replay = load_scorecard_journal(root)
    groups: dict[str, list[GateTransition]] = {}
    for transition in replay.transitions:
        groups.setdefault(transition.identity.agent_key, []).append(transition)
    observation_replay = load_observations(root)
    activated_at = min(
        (item.occurred_at for item in replay.transitions), default=None
    )
    rows, rows_complete = _rows(
        groups,
        observation_replay.observations,
        activated_at=activated_at,
        complete=replay.complete and observation_replay.complete,
    )
    complete = replay.complete and observation_replay.complete and rows_complete
    if not complete:
        for row in rows:
            row["complete"] = False
    rows = _filtered_rows(rows, args)
    result: JsonObject = {
        "complete": complete,
        "period": (
            {"mode": "session", "session_id": str(args.session)}
            if args.session
            else {"mode": "all"}
            if args.all
            else {"mode": "days", "days": int(args.days or DEFAULT_DAYS)}
        ),
        "verification": {
            "ok": sum(_integer(row.get("verification_ok")) for row in rows),
            "fail": sum(_integer(row.get("verification_fail")) for row in rows),
        },
        "sessions": rows,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(_human(result))
    return 0


def _rows(
    groups: dict[str, list[GateTransition]],
    observations: dict[str, JsonObject],
    *,
    activated_at: datetime | None,
    complete: bool,
) -> tuple[list[JsonObject], bool]:
    rows: list[JsonObject] = []
    rows_complete = True
    keys = set(groups) | set(observations)
    for key in sorted(keys):
        transitions = groups.get(key, [])
        observation = observations.get(key)
        try:
            aggregate = _aggregate_row(
                transitions, observation, activated_at=activated_at, complete=complete
            )
        except ScorecardSchemaError:
            rows_complete = False
            continue
        rows.append(_row_json(aggregate, transitions, observation))
    return rows, rows_complete


def _aggregate_row(
    transitions: list[GateTransition], observation: JsonObject | None, *,
    activated_at: datetime | None, complete: bool,
) -> ScorecardAggregate:
    if transitions:
        return aggregate_transitions(transitions, complete=complete)
    if observation is None:
        raise ScorecardSchemaError("observation", "must exist")
    identity = SessionIdentity(
        str(observation["host"]),
        str(observation["session_id"]),
        str(observation["agent"]),
    )
    started = datetime.fromisoformat(str(observation["first_at"])).astimezone(UTC)
    activation = activated_at or datetime.max.replace(tzinfo=UTC)
    return empty_aggregate(
        identity,
        activated_at=activation,
        session_started_at=started,
        complete=complete,
    )


def _row_json(
    aggregate: ScorecardAggregate,
    transitions: list[GateTransition],
    observation: JsonObject | None,
) -> JsonObject:
    unattributed = any(
        item.attribution is Attribution.LEGACY_DEFAULT for item in transitions
    )
    return {
        "host": aggregate.identity.host,
        "session_id": aggregate.identity.session_id,
        "agent": aggregate.identity.agent,
        "attribution": "unattributed" if unattributed else "exact",
        "observed": aggregate.observed,
        "complete": aggregate.complete,
        "blocked_attempts": aggregate.blocked_attempts,
        "recovered_scopes": aggregate.recovered_scopes,
        "resolved_attempts": aggregate.resolved_attempts,
        "cap_allows": aggregate.cap_allows,
        "verification_ok": _integer(
            observation.get("verification_ok") if observation else None
        ),
        "verification_fail": _integer(
            observation.get("verification_fail") if observation else None
        ),
        "first_observed_at": min(
            _observed_times(aggregate.first_occurred_at, observation, "first_at"),
            default=None,
        ),
        "last_observed_at": max(
            _observed_times(aggregate.last_occurred_at, observation, "last_at"),
            default=None,
        ),
        "by_reason": {
            row.reason_code.value: {
                "blocked_attempts": row.blocked_attempts,
                "recovered_scopes": row.recovered_scopes,
                "resolved_attempts": row.resolved_attempts,
                "cap_allows": row.cap_allows,
            }
            for row in aggregate.by_reason
        },
    }


def _observed_times(
    occurred_at: datetime | None, observation: JsonObject | None, field: str
) -> list[str]:
    values = [occurred_at.isoformat()] if occurred_at is not None else []
    if observation is not None:
        values.append(str(observation[field]))
    return values


def _filtered_rows(rows: list[JsonObject], args: argparse.Namespace) -> list[JsonObject]:
    if args.session:
        return [row for row in rows if row.get("session_id") == args.session]
    if args.all:
        return rows
    cutoff = datetime.now(UTC) - timedelta(days=args.days or DEFAULT_DAYS)
    return [row for row in rows if _row_is_recent(row, cutoff)]


def _row_is_recent(row: JsonObject, cutoff: datetime) -> bool:
    value = row.get("last_observed_at")
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).astimezone(UTC) >= cutoff
    except ValueError:
        return False


def _human(result: JsonObject) -> str:
    lines = [
        f"세션 품질 Scorecard · complete={str(result['complete']).lower()}",
        _human_period(result.get("period")),
        "차단 시도 / 회복 턴 / 해결 시도 / cap 통과(미해결) / 검증 성공·실패",
    ]
    sessions = result.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        return "\n".join([*lines, "표시할 세션이 없습니다."])
    for raw in sessions:
        if not isinstance(raw, dict):
            continue
        observed = "관측" if raw.get("observed") is True else "미관측(N/A)"
        attribution = "미귀속" if raw.get("attribution") == "unattributed" else "정확"
        identity = f"{raw.get('host')} / {raw.get('session_id')} / {raw.get('agent')}"
        completeness = f"complete={str(raw.get('complete') is True).lower()}"
        verification = f"검증 {raw.get('verification_ok')}/{raw.get('verification_fail')}"
        if raw.get("observed") is not True:
            lines.append(
                f"{identity} · {attribution} · {observed} · {completeness} · {verification}"
            )
            continue
        lines.append(
            f"{identity} · {attribution} · {observed} · {completeness} · "
            f"차단 {raw.get('blocked_attempts')} · "
            f"회복 {raw.get('recovered_scopes')} · 해결 {raw.get('resolved_attempts')} · "
            f"cap 통과(미해결) {raw.get('cap_allows')} · {verification}"
        )
        reasons = raw.get("by_reason")
        if isinstance(reasons, dict):
            for reason, values in sorted(reasons.items()):
                if isinstance(values, dict):
                    lines.append(
                        f"  {reason} · 차단 {values.get('blocked_attempts')} · "
                        f"회복 {values.get('recovered_scopes')} · "
                        f"해결 {values.get('resolved_attempts')} · "
                        f"cap 통과(미해결) {values.get('cap_allows')}"
                    )
    return "\n".join(lines)


def _human_period(value: JsonValue | None) -> str:
    if not isinstance(value, dict):
        return "기간 · unknown"
    mode = value.get("mode")
    if mode == "session":
        return f"기간 · session={value.get('session_id')}"
    if mode == "days":
        return f"기간 · 최근 {value.get('days')}일"
    return "기간 · 전체"


def _integer(value: JsonValue | None) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _run_agents_view(root: Path, args: argparse.Namespace) -> int:
    gate_replay = load_scorecard_journal(root)
    coordination_replay = load_coordination_journal(root)
    transitions = _filtered_transitions(list(gate_replay.transitions), args)
    events = _filtered_coordination_events(list(coordination_replay.events), args)
    rows: dict[str, JsonObject] = {}
    sessions: dict[str, set[str]] = {}
    for transition in transitions:
        key = f"{transition.identity.host}:{transition.identity.agent}"
        row = rows.setdefault(key, _empty_agent_row(transition.identity))
        sessions.setdefault(key, set()).add(transition.identity.session_id)
        if transition.action is GateAction.BLOCK:
            row["blocked_attempts"] = _integer(row.get("blocked_attempts")) + 1
        elif transition.action is GateAction.RECOVER:
            row["recovered_scopes"] = _integer(row.get("recovered_scopes")) + 1
        elif transition.action is GateAction.CAP_ALLOW:
            row["cap_allows"] = _integer(row.get("cap_allows")) + 1
    for event in events:
        key = f"{event.actor.host}:{event.actor.agent}"
        row = rows.setdefault(key, _empty_agent_row(event.actor))
        sessions.setdefault(key, set()).add(event.actor.session_id)
        if event.category is CoordinationCategory.R2_DENY:
            row["r2_denies"] = _integer(row.get("r2_denies")) + 1
        elif event.category is CoordinationCategory.TURN_BOOTSTRAP:
            field = (
                "turn_bootstrap_entered"
                if event.outcome is CoordinationOutcome.ENTERED
                else "turn_bootstrap_recovered"
            )
            row[field] = _integer(row.get(field)) + 1
    ordered: list[JsonObject] = []
    for key in sorted(rows):
        row = rows[key]
        row["sessions"] = len(sessions.get(key, set()))
        row["complete"] = gate_replay.complete and coordination_replay.complete
        ordered.append(row)
    result: JsonObject = {
        "view": "agents",
        "complete": gate_replay.complete and coordination_replay.complete,
        "period": _period_json(args),
        "agents": ordered,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(_human_agents(result))
    return 0


def _run_coordination_view(root: Path, args: argparse.Namespace) -> int:
    replay = load_coordination_journal(root)
    events = _filtered_coordination_events(list(replay.events), args)
    grouped: dict[tuple[str, str, str, str, str], JsonObject] = {}
    for event in events:
        key = (
            event.actor.agent_key,
            event.subject_agent_key or "",
            event.category.value,
            event.outcome.value,
            event.reason_code.value,
        )
        row = grouped.setdefault(
            key,
            {
                "host": event.actor.host,
                "session_id": event.actor.session_id,
                "agent": event.actor.agent,
                "subject_agent_key": event.subject_agent_key,
                "category": event.category.value,
                "outcome": event.outcome.value,
                "reason_code": event.reason_code.value,
                "count": 0,
                "first_observed_at": event.occurred_at.isoformat(),
                "last_observed_at": event.occurred_at.isoformat(),
            },
        )
        row["count"] = _integer(row.get("count")) + 1
        row["last_observed_at"] = event.occurred_at.isoformat()
    result: JsonObject = {
        "view": "coordination",
        "complete": replay.complete,
        "period": _period_json(args),
        "coordination": [grouped[key] for key in sorted(grouped)],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(_human_coordination(result))
    return 0


def _empty_agent_row(identity: SessionIdentity) -> JsonObject:
    return {
        "host": identity.host,
        "agent": identity.agent,
        "sessions": 0,
        "complete": True,
        "blocked_attempts": 0,
        "recovered_scopes": 0,
        "cap_allows": 0,
        "r2_denies": 0,
        "turn_bootstrap_entered": 0,
        "turn_bootstrap_recovered": 0,
    }


def _filtered_transitions(
    transitions: list[GateTransition], args: argparse.Namespace
) -> list[GateTransition]:
    if args.session:
        return [
            item for item in transitions if item.identity.session_id == args.session
        ]
    if args.all:
        return transitions
    cutoff = datetime.now(UTC) - timedelta(days=args.days or DEFAULT_DAYS)
    return [item for item in transitions if item.occurred_at >= cutoff]


def _filtered_coordination_events(
    events: list[CoordinationEvent], args: argparse.Namespace
) -> list[CoordinationEvent]:
    if args.session:
        return [item for item in events if item.actor.session_id == args.session]
    if args.all:
        return events
    cutoff = datetime.now(UTC) - timedelta(days=args.days or DEFAULT_DAYS)
    return [item for item in events if item.occurred_at >= cutoff]


def _period_json(args: argparse.Namespace) -> JsonObject:
    if args.session:
        return {"mode": "session", "session_id": str(args.session)}
    if args.all:
        return {"mode": "all"}
    return {"mode": "days", "days": int(args.days or DEFAULT_DAYS)}


def _human_agents(result: JsonObject) -> str:
    lines = [
        f"에이전트 품질 Scorecard · complete={str(result['complete']).lower()}",
        _human_period(result.get("period")),
    ]
    agents = result.get("agents")
    if not isinstance(agents, list) or not agents:
        return "\n".join([*lines, "표시할 에이전트가 없습니다."])
    for row in agents:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"{row.get('host')} / {row.get('agent')} · 세션 {row.get('sessions')} · "
            f"차단 {row.get('blocked_attempts')} · 회복 {row.get('recovered_scopes')} · "
            f"cap {row.get('cap_allows')} · R2 deny {row.get('r2_denies')} · "
            f"bootstrap {row.get('turn_bootstrap_entered')}/{row.get('turn_bootstrap_recovered')}"
        )
    return "\n".join(lines)


def _human_coordination(result: JsonObject) -> str:
    lines = [
        f"Coordination Scorecard · complete={str(result['complete']).lower()}",
        _human_period(result.get("period")),
    ]
    period = result.get("period")
    if isinstance(period, dict) and period.get("mode") != "all":
        lines.append(
            "참고: 기간·세션 필터는 entered/recovered를 독립적으로 적용하므로 "
            "대응 이벤트가 선택 범위 밖일 수 있습니다."
        )
    rows = result.get("coordination")
    if not isinstance(rows, list) or not rows:
        return "\n".join([*lines, "표시할 coordination 이벤트가 없습니다."])
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"{row.get('host')} / {row.get('session_id')} / {row.get('agent')} · "
            f"subject={row.get('subject_agent_key') or '-'} · {row.get('category')} · "
            f"{row.get('outcome')} · {row.get('reason_code')} · count={row.get('count')}"
        )
    return "\n".join(lines)
