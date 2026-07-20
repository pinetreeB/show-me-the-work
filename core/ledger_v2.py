"""# noqa: SIZE_OK  — indivisible v2 event reducer; the F1 card forbids a new production module"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from typing import Final, cast

from .ledger_schema import JsonObject, JsonValue
from .ledger_v1 import V1_PROJECTION_FIELDS, apply_v1_event, default_ledger, sequence_value
from .provenance_policy import (
    PROJECT_PATH_IN_ROOT,
    canonical_manifest_key,
    canonicalize_project_path,
)
from .provenance_types import (
    ProvenanceReason,
    ProvenanceStatus,
    normalize_budget_breach_path,
    normalize_budget_top_paths,
)
from .verification_covers import agent_key, attach_covers, record_path_revisions

SNAPSHOT_UNAVAILABLE: Final = "snapshot:unavailable"
EVENT_ONLY_PROMPT: Final = "(event-only)"
NO_TOOL_OUTPUT: Final = "(no tool output)"
MAX_ATTRIBUTION_PATHS: Final = 10_000
MAX_PATH_OWNERS: Final = 8
MAX_CLOSED_TURNS: Final = 256
INVOCATION_LEASE: Final = timedelta(minutes=30)
TURN_STALE_AFTER: Final = timedelta(hours=24)


def default_v2_ledger() -> JsonObject:
    ledger = default_ledger()
    ledger["schema_version"] = 2
    ledger["prompt"] = EVENT_ONLY_PROMPT
    ledger["agent"] = "default"
    ledger["manifest_generation"] = 0
    ledger["active_turns"] = {}
    ledger["closed_turns"] = []
    ledger["coordination_outbox"] = {}
    ledger["coordination_degraded"] = False
    ledger["coordination_drain_cursor"] = 0
    ledger["coordination_delivered"] = {}
    ledger["coordination_delivered_order"] = []
    ledger["path_attribution"] = {}
    ledger["attribution_capacity_exceeded"] = False
    ledger["attribution_degraded"] = False
    return ledger


def lookup_path_attribution(
    ledger: dict[str, JsonValue],
    canonical_path: str,
) -> dict[str, JsonValue] | None:
    index = ledger.get("path_attribution")
    if not isinstance(index, dict):
        return None
    entry = _lookup_attribution_entry(index, canonical_path)
    if entry is None:
        return None
    return entry | {
        "owners": [
            {
                key: value
                for key, value in owner.items()
                if key != "manifest_generation"
            }
            for owner in _owner_list(entry)
        ]
    }


def attribution_health(ledger: dict[str, JsonValue]) -> JsonObject:
    return {
        "degraded": ledger.get("attribution_degraded") is True,
        "capacity_exceeded": ledger.get("attribution_capacity_exceeded") is True,
    }


def open_peer_invocation_candidates(
    ledger: Mapping[str, JsonValue],
    caller_agent_key: str,
    root: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, JsonObject]:
    observed_at = now or datetime.now(UTC)
    candidates: dict[str, JsonObject] = {}
    active = ledger.get("active_turns")
    if not isinstance(active, dict):
        return candidates
    for peer_key, raw_turn in active.items():
        if peer_key == caller_agent_key or not isinstance(peer_key, str) or not isinstance(raw_turn, dict):
            continue
        records = raw_turn.get("invocations")
        if not isinstance(records, dict):
            continue
        for invocation_id, raw_entry in records.items():
            if not isinstance(invocation_id, str) or not isinstance(raw_entry, dict):
                continue
            if raw_entry.get("status") != "open":
                continue
            started_seq = raw_entry.get("started_seq")
            if (
                not isinstance(started_seq, int)
                or isinstance(started_seq, bool)
                or started_seq <= 0
            ):
                continue
            started_at = _parse_timestamp(raw_entry.get("started_at"))
            if started_at is None or observed_at - started_at > INVOCATION_LEASE:
                continue
            paths = raw_entry.get("candidate_paths")
            if not isinstance(paths, list):
                continue
            evidence: JsonObject = {
                "peer_agent_key": peer_key,
                "peer_turn_id": _string(raw_turn.get("turn_id"), ""),
                "invocation_id": invocation_id,
                "started_seq": started_seq,
                "started_at": started_at.isoformat(),
            }
            for path in paths:
                if not isinstance(path, str) or not path:
                    continue
                # Read-side candidates must go through the same project-relative
                # canonicalization as writes (_canonical_candidate_paths). A live
                # ledger can still hold an open invocation recorded before that
                # write-side fix existed -- with a raw, possibly-absolute path -- and
                # a bare casefold of that string would never match a caller's
                # relative R2 target. out_of_root/unresolvable candidates are not
                # usable as mitigation evidence (unresolvable has no canonical key to
                # index by; out_of_root is not this project's concern, matching R2's
                # own target handling), so they are dropped rather than kept as a
                # stale/misleading key.
                disposition, canonical = canonicalize_project_path(root, path)
                if disposition != PROJECT_PATH_IN_ROOT or canonical is None:
                    continue
                candidates[canonical] = evidence
    return candidates


def refresh_v1_projection(ledger: JsonObject, turn: JsonObject) -> JsonObject:
    global_sequence = sequence_value(ledger.get("event_seq"))
    for field in V1_PROJECTION_FIELDS:
        if field in turn:
            ledger[field] = turn[field]
        elif field.startswith("design_"):
            _ = ledger.pop(field, None)
    # Legacy projection counters are conservative maxima; active turn fields remain authoritative.
    ledger["last_change_seq"] = _max_active_field(ledger, "last_change_seq")
    ledger["stop_blocks"] = _max_active_stop_blocks(ledger)
    ledger["event_seq"] = global_sequence
    return ledger


def apply_v2_event(ledger: JsonObject, payload: Mapping[str, JsonValue]) -> JsonObject:
    event_seq = sequence_value(payload.get("seq"))
    ledger["event_seq"] = max(sequence_value(ledger.get("event_seq")), event_seq)
    event_time = _event_time(payload)
    event = payload.get("event")
    if event == "change":
        commit_state = payload.get("commit_state")
        if commit_state == "uncommitted":
            return ledger
        incoming_generation = payload.get("manifest_generation")
        current_generation = sequence_value(ledger.get("manifest_generation"))
        if (
            isinstance(incoming_generation, int)
            and not isinstance(incoming_generation, bool)
            and incoming_generation < current_generation
        ):
            return ledger
    active = _active_turns(ledger)
    key = _event_turn_key(active, payload, event)
    requested_turn_id = payload.get("turn_id")
    raw_before_gc = active.get(key)
    if (
        event != "prompt"
        and isinstance(raw_before_gc, dict)
        and isinstance(requested_turn_id, str)
        and requested_turn_id
        and raw_before_gc.get("turn_id") != requested_turn_id
    ):
        return ledger
    if (
        event != "prompt"
        and not isinstance(raw_before_gc, dict)
        and isinstance(requested_turn_id, str)
        and turn_is_closed(ledger, key, requested_turn_id)
    ):
        return ledger
    preserve_key = (
        key
        if isinstance(raw_before_gc, dict)
        and raw_before_gc.get("turn_id") == requested_turn_id
        else None
    )
    _gc_stale_turns(ledger, event_time, preserve_key)
    active = _active_turns(ledger)
    if event == "turn_finished":
        raw_turn = active.get(key)
        if (
            isinstance(raw_turn, dict)
            and raw_turn.get("turn_id") == payload.get("turn_id")
        ):
            _close_open_invocations(raw_turn, payload, event_seq, event_time)
            _remember_closed_turn(ledger, key, raw_turn, event_seq)
            _ = active.pop(key, None)
        ledger["active_turns"] = active
        return ledger
    if event == "prompt":
        _forget_closed_turn(ledger, key, payload.get("turn_id"))
        _discard_legacy_turn(active)
        raw_turn = active.get(key)
        same_turn_started_at = (
            raw_turn.get("started_at")
            if isinstance(raw_turn, dict)
            and raw_turn.get("turn_id") == requested_turn_id
            and isinstance(raw_turn.get("started_at"), str)
            else None
        )
        if (
            isinstance(raw_turn, dict)
            and raw_turn.get("turn_id") != requested_turn_id
        ):
            _remember_closed_turn(ledger, key, raw_turn, event_seq)
        same_degraded_turn = (
            isinstance(raw_turn, dict)
            and raw_turn.get("baseline_status") == "degraded"
            and raw_turn.get("turn_id") == payload.get("turn_id")
        )
        if same_degraded_turn:
            turn = raw_turn
            _ = apply_v1_event(turn, payload)
            _update_turn_after_event(turn, payload, ledger, event_time)
            _preserve_degraded_baseline(turn)
        else:
            turn = _new_turn(payload, event_seq)
            if same_turn_started_at is not None:
                turn["started_at"] = same_turn_started_at
    else:
        raw_turn = active.get(key)
        if isinstance(raw_turn, dict):
            turn = raw_turn
            _ = apply_v1_event(turn, payload)
        else:
            turn = _new_turn(payload, event_seq)
        if event == "change":
            _record_path_attribution(ledger, payload)
        _update_turn_after_event(turn, payload, ledger, event_time)
        _preserve_degraded_baseline(turn)
    active[key] = turn
    ledger["active_turns"] = active
    manifest_generation = payload.get("manifest_generation")
    if (
        event == "change"
        and isinstance(manifest_generation, int)
        and not isinstance(manifest_generation, bool)
    ):
        ledger["manifest_generation"] = max(
            sequence_value(ledger.get("manifest_generation")),
            manifest_generation,
        )
    if event == "verification":
        _settle_verified_attribution(ledger, payload)
    _complete_v2_projection(turn)
    return refresh_v1_projection(ledger, turn)


def _record_path_attribution(
    ledger: JsonObject,
    payload: Mapping[str, JsonValue],
) -> None:
    owner = payload.get("owner")
    event_agent = payload.get("agent")
    generation = payload.get("manifest_generation")
    if (
        not isinstance(owner, str)
        or not owner
        or owner != event_agent
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation <= 0
    ):
        return
    index = _attribution_index(ledger)
    live_paths = _live_path_count(index)
    for raw_path in _path_items(payload):
        path = raw_path.get("path")
        if not isinstance(path, str) or not path:
            continue
        key = canonical_manifest_key(path, os.name == "nt")
        existing = _lookup_attribution_entry(index, key)
        if existing is not None and generation < sequence_value(existing.get("generation")):
            continue
        was_live = existing is not None and _entry_is_live(existing)
        after = raw_path.get("after")
        if after is None:
            _ = index.pop(key, None)
            if was_live:
                live_paths -= 1
            continue
        if not isinstance(after, str) or not after:
            continue
        if existing is None:
            if live_paths >= MAX_ATTRIBUTION_PATHS:
                ledger["attribution_capacity_exceeded"] = True
                continue
            if len(index) >= MAX_ATTRIBUTION_PATHS:
                aged_key = next(
                    (
                        candidate_key
                        for candidate_key, candidate in index.items()
                        if isinstance(candidate, dict)
                        and not _entry_is_live(candidate)
                    ),
                    None,
                )
                if aged_key is None:
                    ledger["attribution_capacity_exceeded"] = True
                    continue
                _ = index.pop(aged_key, None)
            created: JsonObject = {
                "generation": generation,
                "status": "exclusive",
                "owners": [],
            }
            index[key] = created
            existing = created
        elif not was_live and live_paths >= MAX_ATTRIBUTION_PATHS:
            ledger["attribution_capacity_exceeded"] = True
            continue
        _record_owner(existing, payload, raw_path)
        if not was_live and _entry_is_live(existing):
            live_paths += 1


def _record_owner(
    entry: JsonObject,
    payload: Mapping[str, JsonValue],
    raw_path: JsonObject,
) -> None:
    generation = sequence_value(payload.get("manifest_generation"))
    after = _string(raw_path.get("after"), "")
    owners = _owner_list(entry)
    key = agent_key(payload)
    revision_seq = sequence_value(payload.get("seq"))
    current_index = next(
        (
            index
            for index, owner in enumerate(owners)
            if owner.get("agent_key") == key
        ),
        None,
    )
    if current_index is not None:
        current_seq = sequence_value(owners[current_index].get("revision_seq"))
        if revision_seq < current_seq:
            return
    else:
        if len(owners) >= MAX_PATH_OWNERS:
            settled_index = next(
                (
                    index
                    for index, owner in sorted(
                        enumerate(owners),
                        key=lambda item: sequence_value(item[1].get("revision_seq")),
                    )
                    if owner.get("settled") is True
                ),
                None,
            )
            if settled_index is not None:
                _ = owners.pop(settled_index)
            else:
                entry["generation"] = generation
                entry["status"] = "contended"
                entry["overflow"] = True
                return
    revision: JsonObject = {
        "agent_key": key,
        "turn_id": _string(payload.get("turn_id"), f"turn-{revision_seq}"),
        "revision_seq": revision_seq,
        "manifest_generation": generation,
        "after_digest": after,
        "invocation_id": _string(payload.get("invocation_id"), f"change-{revision_seq}"),
        "settled": False,
    }
    if current_index is None or current_index >= len(owners):
        owners.append(revision)
    else:
        owners[current_index] = revision
    for candidate in owners:
        if candidate.get("agent_key") != key and candidate.get("after_digest") != after:
            candidate["settled"] = True
    entry["generation"] = generation
    entry["owners"] = owners
    _refresh_attribution_status(
        entry,
        payload.get("attribution_status") == "contended",
    )


def _settle_verified_attribution(
    ledger: JsonObject,
    payload: Mapping[str, JsonValue],
) -> None:
    if payload.get("success") is not True:
        return
    covers = payload.get("covers")
    if not isinstance(covers, dict):
        return
    through_seq = sequence_value(covers.get("through_seq"))
    verification_seq = sequence_value(payload.get("seq"))
    key = agent_key(payload)
    index = _attribution_index(ledger)
    for revision in _path_items(covers, "path_revisions"):
        path = revision.get("path")
        after = revision.get("after")
        if not isinstance(path, str) or not isinstance(after, str):
            continue
        entry = _lookup_attribution_entry(
            index,
            canonical_manifest_key(path, os.name == "nt"),
        )
        if entry is None:
            continue
        changed = False
        for owner in _owner_list(entry):
            revision_seq = sequence_value(owner.get("revision_seq"))
            if (
                owner.get("agent_key") == key
                and owner.get("after_digest") == after
                and revision_seq <= through_seq
                and revision_seq < verification_seq
            ):
                owner["settled"] = True
                changed = True
        if changed:
            _refresh_attribution_status(entry, False)


def _attribution_index(ledger: JsonObject) -> JsonObject:
    value = ledger.get("path_attribution")
    if isinstance(value, dict):
        return value
    index: JsonObject = {}
    ledger["path_attribution"] = index
    return index


def _lookup_attribution_entry(
    index: Mapping[str, JsonValue],
    canonical_path: str,
) -> JsonObject | None:
    entry = index.get(canonical_path)
    return entry if isinstance(entry, dict) else None


def _path_items(
    payload: Mapping[str, JsonValue],
    field: str = "paths",
) -> list[JsonObject]:
    value = payload.get(field)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _owner_list(entry: JsonObject) -> list[JsonObject]:
    value = entry.get("owners")
    if not isinstance(value, list):
        return []
    return [owner for owner in value if isinstance(owner, dict)]


def _entry_is_live(entry: JsonObject) -> bool:
    return any(owner.get("settled") is False for owner in _owner_list(entry))


def _live_path_count(index: JsonObject) -> int:
    return sum(
        1
        for entry in index.values()
        if isinstance(entry, dict) and _entry_is_live(entry)
    )


def _refresh_attribution_status(entry: JsonObject, source_contended: bool) -> None:
    unsettled = sum(
        1 for owner in _owner_list(entry) if owner.get("settled") is False
    )
    entry["status"] = (
        "contended"
        if source_contended or entry.get("overflow") is True or unsettled > 1
        else "exclusive"
    )


def _active_turns(ledger: JsonObject) -> JsonObject:
    active = ledger.get("active_turns")
    return active if isinstance(active, dict) else {}


def _event_turn_key(
    active: JsonObject,
    payload: Mapping[str, JsonValue],
    event: JsonValue | None,
) -> str:
    exact = agent_key(payload)
    if event == "prompt" or isinstance(active.get(exact), dict):
        return exact
    legacy = payload.get("attribution") == "legacy_default" or payload.get("identity_synthetic") is True
    if not legacy:
        return exact
    candidates = [
        key
        for key, value in active.items()
        if isinstance(key, str) and isinstance(value, dict)
    ]
    return candidates[0] if len(candidates) == 1 else exact


def _max_active_field(ledger: JsonObject, field: str) -> int:
    return max(
        (
            sequence_value(turn.get(field))
            for turn in _active_turns(ledger).values()
            if isinstance(turn, dict)
        ),
        default=0,
    )


def _max_active_stop_blocks(ledger: JsonObject) -> int:
    return max(
        (
            sequence_value(blocks.get("stop"))
            for turn in _active_turns(ledger).values()
            if isinstance(turn, dict)
            and isinstance(blocks := turn.get("blocks"), dict)
        ),
        default=0,
    )


def _new_turn(payload: Mapping[str, JsonValue], event_seq: int) -> JsonObject:
    event_payload = dict(payload)
    event_payload["seq"] = event_seq
    turn = apply_v1_event(default_ledger(), event_payload)
    turn["turn_id"] = _string(payload.get("turn_id"), f"turn-{event_seq}")
    turn["start_seq"] = event_seq
    turn["baseline_snapshot_id"] = _string(
        payload.get("baseline_snapshot_id"), SNAPSHOT_UNAVAILABLE
    )
    turn["current_snapshot_id"] = _string(
        payload.get("current_snapshot_id"), SNAPSHOT_UNAVAILABLE
    )
    turn["pending_change_ids"] = []
    turn["blocks"] = {"stop": 0}
    turn["agent"] = _string(payload.get("agent"), "default")
    event_time = _event_time(payload)
    turn["started_at"] = event_time.isoformat()
    turn["last_event_at"] = event_time.isoformat()
    baseline = turn["baseline_snapshot_id"]
    missing = payload.get("provenance_incomplete") is True or (
        "baseline_snapshot_id" in payload and baseline == SNAPSHOT_UNAVAILABLE
    )
    turn["baseline_status"] = "missing" if missing else "ready"
    if missing:
        turn["provenance_status_reason"] = "turn_not_started"
    _update_turn_after_event(turn, payload, None, event_time)
    _preserve_degraded_baseline(turn)
    return turn


def _update_turn_after_event(
    turn: JsonObject,
    payload: Mapping[str, JsonValue],
    ledger: JsonObject | None = None,
    event_time: datetime | None = None,
) -> None:
    observed_at = event_time or _event_time(payload)
    event_seq = sequence_value(payload.get("seq"))
    bootstrap_recovered = _applied_bootstrap_recovery(turn, payload)
    turn["last_event_at"] = observed_at.isoformat()
    if bootstrap_recovered:
        if "bootstrap_recovered_at" not in turn:
            turn["bootstrap_recovered_at"] = observed_at.isoformat()
        invocation_id = payload.get("invocation_id")
        if "bootstrap_recovery_evidence_refs" not in turn:
            turn["bootstrap_recovery_evidence_refs"] = (
                [f"invocation:{invocation_id}"]
                if isinstance(invocation_id, str) and invocation_id
                else []
            )
    _close_open_invocations(turn, payload, event_seq, observed_at)
    was_degraded = turn.get("baseline_status") == "degraded"
    baseline_status = payload.get("baseline_status")
    if was_degraded:
        baseline_status = "degraded"
    if baseline_status in {"missing", "ready", "degraded"}:
        turn["baseline_status"] = baseline_status
    baseline_snapshot = payload.get("baseline_snapshot_id")
    if not was_degraded and isinstance(baseline_snapshot, str) and baseline_snapshot:
        turn["baseline_snapshot_id"] = baseline_snapshot
    snapshot_id = payload.get("current_snapshot_id")
    if (
        not (was_degraded and payload.get("baseline_status") == "ready")
        and isinstance(snapshot_id, str)
        and snapshot_id
    ):
        turn["current_snapshot_id"] = snapshot_id
    incomplete = payload.get("provenance_incomplete")
    if isinstance(incomplete, bool):
        turn["provenance_incomplete"] = incomplete
    status = payload.get("provenance_status")
    if isinstance(status, str) and status:
        turn["provenance_status"] = status
    status_reason = payload.get("provenance_status_reason")
    if isinstance(status_reason, str):
        turn["provenance_status_reason"] = (
            "turn_not_started"
            if turn.get("baseline_status") == "missing"
            and status_reason in {"", "observation_error"}
            else status_reason
        )
    if "provenance_budget_top_paths" in payload:
        top_paths = normalize_budget_top_paths(payload.get("provenance_budget_top_paths"))
        if top_paths:
            turn["provenance_budget_top_paths"] = cast(
                JsonValue,
                [dict(item) for item in top_paths],
            )
        else:
            turn.pop("provenance_budget_top_paths", None)
    if "provenance_budget_breach_path" in payload:
        breach_path = normalize_budget_breach_path(payload.get("provenance_budget_breach_path"))
        if breach_path:
            turn["provenance_budget_breach_path"] = breach_path
        else:
            turn.pop("provenance_budget_breach_path", None)
    if payload.get("provenance_mutation_capable") is True:
        turn["provenance_mutation_capable"] = True
    if payload.get("provenance_remote_mutation") is True:
        turn["provenance_remote_mutation"] = True
        mutation_seq = sequence_value(payload.get("seq"))
        turn["last_remote_mutation_seq"] = mutation_seq
        epochs = turn.get("remote_mutation_epochs")
        epochs = epochs if isinstance(epochs, dict) else {}
        target_ids = payload.get("remote_target_ids")
        if isinstance(target_ids, list):
            for target_id in target_ids:
                if isinstance(target_id, str) and target_id:
                    epochs[target_id] = mutation_seq
        if epochs:
            turn["remote_mutation_epochs"] = epochs
    event = payload.get("event")
    if event == "turn_bootstrap_pending":
        turn["bootstrap_pending"] = True
        return
    if event in {"turn_bootstrap_initialized", "turn_bootstrap_recovered"}:
        turn.pop("bootstrap_pending", None)
        return
    if event == "invocation":
        _remember_invocation(turn, payload)
        return
    if event == "finish_requested":
        turn["finish_state"] = "finish_requested"
        turn["finish_requested_seq"] = event_seq
        turn["finish_requested_at"] = observed_at.isoformat()
        return
    if event == "turn_started":
        if not was_degraded:
            turn["baseline_status"] = "ready"
        return
    if event == "observation":
        return
    if event == "verification":
        covers = payload.get("covers")
        if isinstance(covers, dict):
            attach_covers(turn, covers)
        return
    if event != "change":
        return
    paths = payload.get("paths")
    if isinstance(paths, list):
        _apply_path_projection(turn, payload)
        record_path_revisions(turn, payload, ledger)
        return
    change_id = _change_id(payload)
    if change_id:
        pending = _string_list(turn.get("pending_change_ids"))
        if change_id not in pending:
            pending.append(change_id)
        turn["pending_change_ids"] = pending
    if turn.get("migration_mode") == "legacy_turn":
        turn["legacy_seq_less"] = False


def _applied_bootstrap_recovery(
    turn: Mapping[str, JsonValue],
    payload: Mapping[str, JsonValue],
) -> bool:
    return (
        turn.get("baseline_status") == "missing"
        and payload.get("event") == "turn_bootstrap_recovered"
        and payload.get("turn_bootstrap_recovered") is True
        and payload.get("baseline_status") == "ready"
        and payload.get("provenance_incomplete") is False
        and payload.get("provenance_status") == "complete"
        and payload.get("provenance_status_reason") == ""
    )


def _preserve_degraded_baseline(turn: JsonObject) -> None:
    if turn.get("baseline_status") != "degraded":
        return
    turn["provenance_incomplete"] = True
    turn["provenance_status"] = ProvenanceStatus.INCOMPLETE.value
    turn["provenance_status_reason"] = ProvenanceReason.BASELINE_STATE_MISMATCH.value


def _remember_invocation(turn: JsonObject, payload: Mapping[str, JsonValue]) -> None:
    invocation_id = payload.get("invocation_id")
    if not isinstance(invocation_id, str) or not invocation_id:
        return
    records = turn.get("invocations")
    records = records if isinstance(records, dict) else {}
    candidate_paths = payload.get("candidate_paths")
    entry: JsonObject = {
        "candidate_paths": candidate_paths if isinstance(candidate_paths, list) else [],
        "status": "open",
        "started_seq": sequence_value(payload.get("seq")),
        "started_at": _event_time(payload).isoformat(),
    }
    covers = payload.get("covers")
    if isinstance(covers, dict):
        entry["covers"] = covers
    records[invocation_id] = entry
    turn["invocations"] = records


def _close_open_invocations(
    turn: JsonObject,
    payload: Mapping[str, JsonValue],
    event_seq: int,
    event_time: datetime,
) -> None:
    records = turn.get("invocations")
    if not isinstance(records, dict):
        return
    event = payload.get("event")
    incoming_id = payload.get("invocation_id")
    for invocation_id, raw_entry in records.items():
        if not isinstance(invocation_id, str) or not isinstance(raw_entry, dict):
            continue
        if raw_entry.get("status") != "open":
            continue
        if event == "invocation" and invocation_id == incoming_id:
            continue
        raw_entry["status"] = "closed"
        raw_entry["completed_seq"] = event_seq
        raw_entry["completed_at"] = event_time.isoformat()


def _gc_stale_turns(
    ledger: JsonObject,
    now: datetime,
    preserve_key: str | None = None,
) -> None:
    active = _active_turns(ledger)
    stale = [
        key
        for key, raw_turn in active.items()
        if isinstance(key, str)
        and key != preserve_key
        and isinstance(raw_turn, dict)
        and (last_event := _parse_timestamp(raw_turn.get("last_event_at"))) is not None
        and now - last_event > TURN_STALE_AFTER
    ]
    for key in stale:
        raw_turn = active.pop(key, None)
        if isinstance(raw_turn, dict):
            _remember_closed_turn(
                ledger,
                key,
                raw_turn,
                sequence_value(ledger.get("event_seq")),
            )
    ledger["active_turns"] = active


def turn_is_closed(ledger: Mapping[str, JsonValue], agent: str, turn_id: str) -> bool:
    closed = ledger.get("closed_turns")
    if not isinstance(closed, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("agent_key") == agent
        and item.get("turn_id") == turn_id
        for item in closed
    )


def _remember_closed_turn(
    ledger: JsonObject,
    agent: str,
    turn: Mapping[str, JsonValue],
    event_seq: int,
) -> None:
    turn_id = turn.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id:
        return
    closed = ledger.get("closed_turns")
    entries = [item for item in closed if isinstance(item, dict)] if isinstance(closed, list) else []
    entries = [
        item
        for item in entries
        if item.get("agent_key") != agent or item.get("turn_id") != turn_id
    ]
    entries.append(
        {
            "agent_key": agent,
            "turn_id": turn_id,
            "closed_seq": max(0, event_seq),
        }
    )
    ledger["closed_turns"] = entries[-MAX_CLOSED_TURNS:]


def _forget_closed_turn(
    ledger: JsonObject,
    agent: str,
    turn_id: JsonValue | None,
) -> None:
    if not isinstance(turn_id, str) or not turn_id:
        return
    closed = ledger.get("closed_turns")
    if not isinstance(closed, list):
        return
    ledger["closed_turns"] = [
        item
        for item in closed
        if not (
            isinstance(item, dict)
            and item.get("agent_key") == agent
            and item.get("turn_id") == turn_id
        )
    ]


def _event_time(payload: Mapping[str, JsonValue]) -> datetime:
    return _parse_timestamp(payload.get("timestamp")) or datetime.now(UTC)


def _parse_timestamp(value: JsonValue | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _apply_path_projection(turn: JsonObject, payload: Mapping[str, JsonValue]) -> None:
    paths = payload.get("paths")
    if not isinstance(paths, list):
        return
    for raw_path in paths:
        if not isinstance(raw_path, dict):
            continue
        path = raw_path.get("path")
        if not isinstance(path, str) or not path:
            continue
        event_payload = dict(payload)
        event_payload["path"] = path
        kind = raw_path.get("kind")
        if isinstance(kind, str):
            event_payload["kind"] = kind
        _ = apply_v1_event(turn, event_payload)


def _change_id(payload: Mapping[str, JsonValue]) -> str:
    direct = payload.get("change_id")
    if isinstance(direct, str) and direct:
        return direct
    paths = payload.get("paths")
    if not isinstance(paths, list) or not paths:
        return ""
    first = paths[0]
    if not isinstance(first, dict):
        return ""
    value: JsonValue | None = first.get("change_id")
    return value if isinstance(value, str) else ""


def _discard_legacy_turn(active: JsonObject) -> None:
    legacy = active.get("default")
    if isinstance(legacy, dict) and legacy.get("migration_mode") == "legacy_turn":
        _ = active.pop("default", None)


def _complete_v2_projection(turn: JsonObject) -> None:
    if not _string(turn.get("prompt"), ""):
        turn["prompt"] = EVENT_ONLY_PROMPT
    results = turn.get("verification_results")
    if not isinstance(results, list):
        return
    for result in results:
        if isinstance(result, dict) and not _string(result.get("evidence"), ""):
            result["evidence"] = NO_TOOL_OUTPUT


def _string(value: JsonValue | None, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _string_list(value: JsonValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
