"""Append-only coordination observations kept separate from gate scorecards."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, unique
import json
import os
from pathlib import Path
from typing import Final
from uuid import NAMESPACE_URL, uuid4, uuid5

from .agent_log import ledger_transaction
from .ledger_schema import JsonObject, JsonValue
from .scorecard import Attribution, SessionIdentity
from .state_layout import state_dir


COORDINATION_SCHEMA_VERSION: Final = 1
_EVENT_NAME: Final = "coordination_transition"
_ALLOWED_FIELDS: Final = frozenset(
    {
        "scorecard_coord_schema_version",
        "event",
        "event_id",
        "actor",
        "actor_turn_id",
        "subject_agent_key",
        "category",
        "outcome",
        "reason_code",
        "evidence_refs",
        "attribution",
        "occurred_at",
    }
)


@unique
class CoordinationCategory(StrEnum):
    R2_DENY = "r2_deny"
    PEER_EXCLUSION = "peer_exclusion"
    PEER_CONFLICT = "peer_conflict"
    OWNER_SETTLEMENT = "owner_settlement"
    ATTRIBUTION_HEALTH = "attribution_health"
    TURN_BOOTSTRAP = "turn_bootstrap"
    INVOCATION_LEASE = "invocation_lease"
    QUICK_PROMOTION = "quick_promotion"
    CROSS_EVIDENCE = "cross_evidence"


@unique
class CoordinationOutcome(StrEnum):
    BLOCKED = "blocked"
    AVOIDED_BLOCK = "avoided_block"
    ENTERED = "entered"
    RECOVERED = "recovered"
    SETTLED = "settled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    DEGRADED = "degraded"


@unique
class CoordinationReason(StrEnum):
    ATTRIBUTION_DEGRADED = "attribution_degraded"
    COMMAND_PARSE_UNAVAILABLE = "command_parse_unavailable"
    PEER_UNSETTLED = "peer_unsettled"
    STATE_DIR_PROTECTED = "state_dir_protected"
    UNRESOLVABLE_TARGET = "unresolvable_target"
    TURN_NOT_STARTED = "turn_not_started"
    COMPLETE = "complete"
    PEER_ACTIVITY = "peer_activity"
    PEER_CONFLICT = "peer_conflict"
    OWNER_SETTLED = "owner_settled"
    ATTRIBUTION_HEALTH = "attribution_health"
    INVOCATION_LEASE = "invocation_lease"
    CROSS_EVIDENCE = "cross_evidence"
    QUICK_PROMOTION = "quick_promotion"


@dataclass
class CoordinationSchemaError(ValueError):
    field: str
    requirement: str

    def __str__(self) -> str:
        return f"invalid coordination schema at {self.field}: {self.requirement}"


@dataclass(frozen=True, slots=True)
class CoordinationEvent:
    event_id: str
    actor: SessionIdentity
    actor_turn_id: str
    subject_agent_key: str | None
    category: CoordinationCategory
    outcome: CoordinationOutcome
    reason_code: CoordinationReason
    evidence_refs: tuple[str, ...]
    attribution: Attribution
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class CoordinationReplay:
    events: tuple[CoordinationEvent, ...]
    complete: bool


def coordination_journal_path(project_root: str | Path) -> Path:
    return state_dir(project_root) / "scorecard" / "coordination.jsonl"


def new_coordination_event(
    actor: SessionIdentity,
    actor_turn_id: str,
    category: CoordinationCategory,
    outcome: CoordinationOutcome,
    reason_code: CoordinationReason,
    *,
    subject_agent_key: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    attribution: Attribution = Attribution.EXACT,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
) -> CoordinationEvent:
    raw: JsonObject = {
        "scorecard_coord_schema_version": COORDINATION_SCHEMA_VERSION,
        "event": _EVENT_NAME,
        "event_id": event_id or str(uuid4()),
        "actor": {
            "host": actor.host,
            "session_id": actor.session_id,
            "agent": actor.agent,
        },
        "actor_turn_id": actor_turn_id,
        "subject_agent_key": subject_agent_key,
        "category": category.value,
        "outcome": outcome.value,
        "reason_code": reason_code.value,
        "evidence_refs": list(evidence_refs),
        "attribution": attribution.value,
        "occurred_at": (occurred_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
    }
    return parse_coordination_event(raw)


def coordination_event_json(event: CoordinationEvent) -> JsonObject:
    return {
        "scorecard_coord_schema_version": COORDINATION_SCHEMA_VERSION,
        "event": _EVENT_NAME,
        "event_id": event.event_id,
        "actor": {
            "host": event.actor.host,
            "session_id": event.actor.session_id,
            "agent": event.actor.agent,
        },
        "actor_turn_id": event.actor_turn_id,
        "subject_agent_key": event.subject_agent_key,
        "category": event.category.value,
        "outcome": event.outcome.value,
        "reason_code": event.reason_code.value,
        "evidence_refs": list(event.evidence_refs),
        "attribution": event.attribution.value,
        "occurred_at": event.occurred_at.isoformat(),
    }


def parse_coordination_event(raw: Mapping[str, JsonValue]) -> CoordinationEvent:
    unexpected = set(raw) - _ALLOWED_FIELDS
    missing = _ALLOWED_FIELDS - set(raw)
    if unexpected:
        raise CoordinationSchemaError(
            next(iter(sorted(unexpected))), "field is forbidden"
        )
    if missing:
        raise CoordinationSchemaError(next(iter(sorted(missing))), "field is required")
    coordination_version = raw.get("scorecard_coord_schema_version")
    if (
        coordination_version != COORDINATION_SCHEMA_VERSION
        or isinstance(coordination_version, bool)
    ):
        raise CoordinationSchemaError(
            "scorecard_coord_schema_version", "must equal 1"
        )
    if raw.get("event") != _EVENT_NAME:
        raise CoordinationSchemaError("event", f"must equal {_EVENT_NAME}")
    actor_raw = raw.get("actor")
    if not isinstance(actor_raw, dict) or set(actor_raw) != {
        "host",
        "session_id",
        "agent",
    }:
        raise CoordinationSchemaError("actor", "must contain only host/session_id/agent")
    actor = SessionIdentity(
        _required_string(actor_raw, "host", "actor.host"),
        _required_string(actor_raw, "session_id", "actor.session_id"),
        _required_string(actor_raw, "agent", "actor.agent"),
    )
    subject = raw.get("subject_agent_key")
    if subject is not None and (not isinstance(subject, str) or not subject):
        raise CoordinationSchemaError(
            "subject_agent_key", "must be null or a non-empty string"
        )
    evidence_raw = raw.get("evidence_refs")
    if not isinstance(evidence_raw, list) or not all(
        isinstance(item, str) and item for item in evidence_raw
    ):
        raise CoordinationSchemaError(
            "evidence_refs", "must be a list of non-empty strings"
        )
    if len(evidence_raw) > 32:
        raise CoordinationSchemaError("evidence_refs", "must contain at most 32 items")
    occurred_at = _utc_datetime(raw.get("occurred_at"))
    event = CoordinationEvent(
        _required_string(raw, "event_id", "event_id"),
        actor,
        _required_string(raw, "actor_turn_id", "actor_turn_id"),
        subject,
        _enum(CoordinationCategory, raw.get("category"), "category"),
        _enum(CoordinationOutcome, raw.get("outcome"), "outcome"),
        _enum(CoordinationReason, raw.get("reason_code"), "reason_code"),
        tuple(evidence_raw),
        _enum(Attribution, raw.get("attribution"), "attribution"),
        occurred_at,
    )
    _validate_active_contract(event)
    return event


def load_coordination_journal(project_root: str | Path) -> CoordinationReplay:
    try:
        lines = coordination_journal_path(project_root).read_text(
            encoding="utf-8"
        ).splitlines()
    except FileNotFoundError:
        return CoordinationReplay((), True)
    except (OSError, UnicodeDecodeError):
        return CoordinationReplay((), False)
    events: list[CoordinationEvent] = []
    by_id: dict[str, CoordinationEvent] = {}
    complete = True
    for line in lines:
        if not line.strip():
            continue
        try:
            value: JsonValue = json.loads(line)
            if not isinstance(value, dict):
                raise CoordinationSchemaError("event", "must be an object")
            event = parse_coordination_event(value)
        except (json.JSONDecodeError, CoordinationSchemaError):
            complete = False
            continue
        existing = by_id.get(event.event_id)
        if existing is not None:
            if existing != event:
                complete = False
            continue
        by_id[event.event_id] = event
        events.append(event)
    return CoordinationReplay(tuple(events), complete)


def record_coordination_event(
    project_root: str | Path, event: CoordinationEvent
) -> bool:
    recorded, _canonical = _record_coordination_event(
        project_root,
        event,
        reconcile_stable_r2=False,
    )
    return recorded


def record_coordination_event_for_delivery(
    project_root: str | Path,
    event: CoordinationEvent,
) -> CoordinationEvent:
    """Write an event or return the first canonical stable R2 observation."""
    _recorded, canonical = _record_coordination_event(
        project_root,
        event,
        reconcile_stable_r2=True,
    )
    return canonical


def _record_coordination_event(
    project_root: str | Path,
    event: CoordinationEvent,
    *,
    reconcile_stable_r2: bool,
) -> tuple[bool, CoordinationEvent]:
    root = str(Path(project_root).resolve())
    with ledger_transaction(root):
        replay = load_coordination_journal(root)
        for existing in replay.events:
            if existing.event_id != event.event_id:
                continue
            if existing != event:
                if reconcile_stable_r2 and _same_stable_r2_event(
                    root,
                    existing,
                    event,
                ):
                    return False, existing
                raise CoordinationSchemaError(
                    "event_id", "must not identify conflicting content"
                )
            return False, existing
        _append_coordination_event(root, event)
    return True, event


def try_record_coordination_event(
    project_root: str | Path, event: CoordinationEvent
) -> bool:
    try:
        return record_coordination_event(project_root, event)
    except Exception:  # noqa: BLE001 - observability must never change a gate decision.
        return False


def record_peer_coordination(
    project_root: str | Path,
    event: CoordinationEvent,
) -> bool:
    """Durably accept coordination without making it a gate input."""
    root = str(Path(project_root).resolve())
    try:
        from .ledger import enqueue_coordination_event

        accepted = enqueue_coordination_event(root, coordination_event_json(event))
        if accepted is None:
            _ = record_coordination_event(root, event)
            return True
        return accepted
    except Exception:  # noqa: BLE001 - audit durability cannot change a gate.
        return False


def record_r2_deny_coordination(
    project_root: str | Path,
    event: CoordinationEvent,
) -> bool:
    """Accept an R2 deny audit with one immediate lock attempt and no drain."""
    if event.category is not CoordinationCategory.R2_DENY:
        return False
    root = str(Path(project_root).resolve())
    raw = coordination_event_json(event)
    try:
        from .ledger import _enqueue_coordination_raw, load_ledger, save_ledger
        from .ledger_storage import ledger_path
        from .ledger_v2 import default_v2_ledger

        with ledger_transaction(
            root,
            lock_wait_seconds=0.0,
            release_wait_seconds=0.0,
        ):
            destination = ledger_path(root)
            existed_before_load = destination.exists()
            ledger = load_ledger({"project_root": root})
            if not existed_before_load:
                ledger = default_v2_ledger()
            if ledger.get("schema_version") == 2:
                for field in ("coordination_outbox", "coordination_delivered"):
                    accepted = ledger.get(field)
                    if isinstance(accepted, dict) and event.event_id in accepted:
                        return True
                try:
                    accepted, changed = _enqueue_coordination_raw(ledger, raw)
                except Exception:  # noqa: BLE001 - malformed audit is degraded.
                    accepted = False
                    changed = ledger.get("coordination_degraded") is not True
                    ledger["coordination_degraded"] = True
                saved = not changed or save_ledger({"project_root": root}, ledger)
                return accepted and saved
            return False
    except Exception:  # noqa: BLE001 - audit durability cannot change a gate.
        return False


def load_accepted_peer_coordination(
    project_root: str | Path,
    event_id: str,
) -> CoordinationEvent | None:
    try:
        from .ledger import load_accepted_coordination_event

        root = str(Path(project_root).resolve())
        raw = load_accepted_coordination_event(root, event_id)
        if raw is not None:
            return parse_coordination_event(raw)
        return _stable_journal_event(root, event_id)
    except Exception:  # noqa: BLE001 - a cache miss is safe for observability.
        return None


def _stable_journal_event(
    root: str,
    event_id: str,
) -> CoordinationEvent | None:
    replay = load_coordination_journal(root)
    for event in replay.events:
        if event.event_id != event_id:
            continue
        expected = stable_coordination_event_id(
            root,
            event.actor,
            event.actor_turn_id,
            event.category,
            event.outcome,
            event.reason_code,
            event.evidence_refs,
        )
        return event if expected == event_id else None
    return None


def _same_stable_r2_event(
    root: str,
    first: CoordinationEvent,
    second: CoordinationEvent,
) -> bool:
    if (
        first.category is not CoordinationCategory.R2_DENY
        or second.category is not CoordinationCategory.R2_DENY
        or first.event_id != second.event_id
    ):
        return False
    return all(
        stable_coordination_event_id(
            root,
            event.actor,
            event.actor_turn_id,
            event.category,
            event.outcome,
            event.reason_code,
            event.evidence_refs,
        )
        == event.event_id
        for event in (first, second)
    )


def stable_coordination_event_id(
    project_root: str | Path,
    actor: SessionIdentity,
    actor_turn_id: str,
    category: CoordinationCategory,
    outcome: CoordinationOutcome,
    reason_code: CoordinationReason,
    evidence_refs: tuple[str, ...] = (),
) -> str:
    identity_evidence = (
        () if category is CoordinationCategory.TURN_BOOTSTRAP else evidence_refs
    )
    key = "|".join(
        (
            str(Path(project_root).resolve()),
            actor.agent_key,
            actor_turn_id,
            category.value,
            outcome.value,
            reason_code.value,
            *identity_evidence,
        )
    )
    return str(uuid5(NAMESPACE_URL, key))


def _append_coordination_event(
    project_root: str | Path, event: CoordinationEvent
) -> None:
    path = coordination_journal_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        coordination_event_json(event),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    prefix = b""
    try:
        with path.open("rb") as existing:
            existing.seek(0, 2)
            if existing.tell():
                existing.seek(-1, 2)
                if existing.read(1) != b"\n":
                    prefix = b"\n"
    except FileNotFoundError:
        pass
    with path.open("ab") as handle:
        _ = handle.write(prefix + serialized + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _required_string(
    raw: Mapping[str, JsonValue], key: str, field: str
) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise CoordinationSchemaError(field, "must be a non-empty string")
    return value


def _enum(enum_type, value: JsonValue | None, field: str):
    if not isinstance(value, str):
        raise CoordinationSchemaError(field, "must be a known string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise CoordinationSchemaError(field, "must be a known string") from exc


def _utc_datetime(value: JsonValue | None) -> datetime:
    if not isinstance(value, str):
        raise CoordinationSchemaError("occurred_at", "must be a UTC ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CoordinationSchemaError(
            "occurred_at", "must be a UTC ISO-8601 string"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise CoordinationSchemaError("occurred_at", "must use UTC")
    return parsed.astimezone(UTC)


def _validate_active_contract(event: CoordinationEvent) -> None:
    if event.category is CoordinationCategory.R2_DENY:
        if event.outcome is not CoordinationOutcome.BLOCKED:
            raise CoordinationSchemaError("outcome", "r2_deny must be blocked")
        if event.reason_code not in {
            CoordinationReason.ATTRIBUTION_DEGRADED,
            CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
            CoordinationReason.PEER_UNSETTLED,
            CoordinationReason.STATE_DIR_PROTECTED,
            CoordinationReason.UNRESOLVABLE_TARGET,
        }:
            raise CoordinationSchemaError("reason_code", "invalid r2_deny reason")
    if event.category is CoordinationCategory.TURN_BOOTSTRAP:
        expected = {
            CoordinationOutcome.ENTERED: CoordinationReason.TURN_NOT_STARTED,
            CoordinationOutcome.RECOVERED: CoordinationReason.COMPLETE,
        }.get(event.outcome)
        if expected is None or event.reason_code is not expected:
            raise CoordinationSchemaError(
                "reason_code", "invalid turn_bootstrap outcome/reason pair"
            )
