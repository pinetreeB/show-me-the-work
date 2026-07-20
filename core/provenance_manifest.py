from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Final

from .agent_log import _LedgerTransaction, append_agent_event, ledger_transaction
from .ledger import (
    JsonObject,
    JsonValue,
    _RecordedEvent,
    _record_coordination_after_event,
    _record_event_locked,
    load_ledger,
    save_ledger,
)
from .ledger_storage import ledger_path
from .ledger_v1 import apply_v1_event, sequence_value
from .ledger_v2 import apply_v2_event, default_v2_ledger, turn_is_closed
from .provenance_store import (
    BaselineAdvance,
    BaselineInitialization,
    SnapshotStoreError,
    advance_turn_baseline,
    claim_legacy_turn_baseline,
    initialize_turn_baseline,
    load_turn_baseline,
    load_workspace_current,
    save_workspace_current,
    turn_baseline_path,
    workspace_current_path,
    turn_baseline_has_identity,
)
from .provenance_policy import canonical_manifest_key
from .provenance_snapshot import snapshot_id_for
from .provenance_types import ProvenanceReason, ProvenanceStatus, Snapshot
from .verification_covers import active_turn, agent_key


UNAVAILABLE_SNAPSHOT: Final = "snapshot:unavailable"
JOURNAL_LIMIT: Final = 256


@dataclass(frozen=True, slots=True)
class ManifestView:
    generation: int
    snapshot: Snapshot | None


@dataclass(frozen=True, slots=True)
class ManifestCommit:
    generation: int
    snapshot: Snapshot
    events: tuple[JsonObject, ...]
    baseline: Snapshot | None = None


@dataclass(frozen=True, slots=True)
class BaselineUpdate:
    agent: str
    turn_id: str
    expected_snapshot_id: str
    snapshot: Snapshot
    candidate_keys: frozenset[str]


@dataclass(frozen=True, slots=True)
class TurnBinding:
    agent: str
    turn_id: str


class TurnBootstrapStatus(StrEnum):
    READY = "ready"
    CANDIDATE_REQUIRED = "candidate_required"
    DEGRADED = "degraded"
    STALE_TURN = "stale_turn"


@dataclass(frozen=True, slots=True)
class TurnBootstrap:
    status: TurnBootstrapStatus
    baseline: Snapshot | None
    recovered: bool = False


class TurnEventCommitStatus(StrEnum):
    RECORDED = "recorded"
    RETRY = "retry"
    DEGRADED = "degraded"
    STALE_TURN = "stale_turn"


@dataclass(frozen=True, slots=True)
class TurnEventCommit:
    status: TurnEventCommitStatus
    baseline_snapshot_id: str = ""


class ManifestStoreError(RuntimeError):
    pass


def load_manifest_view(root: Path) -> ManifestView:
    resolved = root.resolve()
    with ledger_transaction(str(resolved)) as transaction:
        try:
            ledger, payload = _load_locked(resolved)
            current = load_workspace_current(resolved)
            ledger = _recover_locked(
                resolved,
                ledger,
                payload,
                current,
                transaction,
            )
            return ManifestView(_generation(ledger), current)
        except SnapshotStoreError as exc:
            raise ManifestStoreError(str(exc)) from exc


def commit_manifest(
    root: Path,
    expected_generation: int,
    expected_snapshot_id: str,
    snapshot: Snapshot,
    event_templates: tuple[JsonObject, ...],
    baseline: BaselineUpdate | None = None,
    required_turn: TurnBinding | None = None,
) -> ManifestCommit | None:
    resolved = root.resolve()
    with ledger_transaction(str(resolved)) as transaction:
        try:
            return _commit_locked(
                resolved,
                expected_generation,
                expected_snapshot_id,
                snapshot,
                event_templates,
                baseline,
                required_turn,
                transaction,
            )
        except SnapshotStoreError as exc:
            raise ManifestStoreError(str(exc)) from exc


def save_manifest_baseline(
    root: Path,
    expected_generation: int,
    snapshot: Snapshot,
    agent: str,
    turn_id: str,
) -> Snapshot:
    resolved = root.resolve()
    with ledger_transaction(str(resolved)) as transaction:
        try:
            ledger, payload = _load_locked(resolved)
            current = load_workspace_current(resolved)
            ledger = _recover_locked(
                resolved,
                ledger,
                payload,
                current,
                transaction,
            )
            existing, existing_error = _physical_baseline(
                resolved,
                agent,
                turn_id,
            )
            if existing_error is not None:
                raise existing_error
            if existing is not None:
                if (
                    not turn_baseline_has_identity(resolved, agent, turn_id)
                    and _baseline_path_collision(
                        resolved,
                        ledger,
                        agent,
                        turn_id,
                    )
                ):
                    raise SnapshotStoreError(
                        workspace_current_path(resolved),
                        "legacy turn baseline collides with another active identity",
                    )
                return existing
            if (
                _generation(ledger) != expected_generation
                or current is None
                or _snapshot_id(current) != snapshot.snapshot_id
            ):
                raise SnapshotStoreError(
                    workspace_current_path(resolved),
                    "turn baseline candidate lost its manifest generation",
                )
            candidate = current
            outcome = initialize_turn_baseline(
                resolved,
                agent,
                turn_id,
                True,
                candidate,
                transaction,
            )
            if outcome is BaselineInitialization.CONFLICT:
                raise SnapshotStoreError(
                    workspace_current_path(resolved),
                    "turn baseline identity or initialization conflict",
                )
            winner = load_turn_baseline(resolved, agent, turn_id)
            if winner is None:
                raise SnapshotStoreError(
                    workspace_current_path(resolved),
                    "turn baseline initialization did not persist a winner",
                )
            return winner
        except SnapshotStoreError as exc:
            raise ManifestStoreError(str(exc)) from exc


def ensure_turn_bootstrap(
    root: Path,
    event_identity: Mapping[str, JsonValue],
    candidate: Snapshot | None,
    *,
    baseline_missing_hint: bool = False,
    require_existing_turn: bool = False,
) -> TurnBootstrap:
    """Resolve physical/ledger bootstrap truth under one short owning lock."""
    del baseline_missing_hint  # A caller hint must never select a recovery transition.
    resolved = root.resolve()
    recorded: _RecordedEvent | None = None
    result: TurnBootstrap
    with ledger_transaction(str(resolved)) as transaction:
        try:
            ledger, payload = _load_locked(resolved)
            current = load_workspace_current(resolved)
            ledger = _recover_locked(
                resolved,
                ledger,
                payload,
                current,
                transaction,
            )
            turn = active_turn(ledger, event_identity)
            owner = agent_key(event_identity)
            turn_id = _identity_string(event_identity, "turn_id")
            active = ledger.get("active_turns")
            raw_turn = active.get(owner) if isinstance(active, dict) else None
            if isinstance(raw_turn, dict) and raw_turn.get("turn_id") != turn_id:
                result = TurnBootstrap(TurnBootstrapStatus.STALE_TURN, None)
                return result
            if turn is None and turn_is_closed(ledger, owner, turn_id):
                result = TurnBootstrap(TurnBootstrapStatus.STALE_TURN, None)
                return result
            if require_existing_turn and turn is None:
                result = TurnBootstrap(TurnBootstrapStatus.STALE_TURN, None)
                return result
            if turn is not None and turn.get("turn_id") != turn_id:
                result = TurnBootstrap(TurnBootstrapStatus.STALE_TURN, None)
                return result
            physical, physical_error = _physical_baseline(
                resolved,
                owner,
                turn_id,
            )
            physical_has_identity = False
            if physical is not None and physical_error is None:
                try:
                    physical_has_identity = turn_baseline_has_identity(
                        resolved,
                        owner,
                        turn_id,
                    )
                except SnapshotStoreError as exc:
                    physical = None
                    physical_error = exc
            collision = (
                physical is not None
                and not physical_has_identity
                and _baseline_path_collision(
                    resolved,
                    ledger,
                    owner,
                    turn_id,
                )
            )
            if (
                physical is not None
                and physical_error is None
                and not physical_has_identity
                and not collision
            ):
                if claim_legacy_turn_baseline(
                    resolved,
                    owner,
                    turn_id,
                    physical.snapshot_id,
                    transaction,
                ):
                    physical, physical_error = _physical_baseline(
                        resolved,
                        owner,
                        turn_id,
                    )
                    physical_has_identity = physical is not None and physical_error is None
                else:
                    physical = None
                    physical_error = SnapshotStoreError(
                        turn_baseline_path(resolved, owner, turn_id),
                        "could not claim legacy turn baseline identity",
                    )
            status = turn.get("baseline_status") if turn is not None else None
            if status == "degraded":
                result = TurnBootstrap(TurnBootstrapStatus.DEGRADED, None)
            elif status == "ready":
                expected = turn.get("baseline_snapshot_id") if turn is not None else None
                if (
                    physical_error is None
                    and physical is not None
                    and isinstance(expected, str)
                    and physical.snapshot_id == expected
                ):
                    result = TurnBootstrap(TurnBootstrapStatus.READY, physical)
                else:
                    recorded = _record_bootstrap_state_locked(
                        resolved,
                        event_identity,
                        transaction,
                        "turn_bootstrap_degraded",
                        "degraded",
                        None,
                    )
                    result = TurnBootstrap(TurnBootstrapStatus.DEGRADED, None)
            elif physical_error is not None or collision:
                recorded = _record_bootstrap_state_locked(
                    resolved,
                    event_identity,
                    transaction,
                    "turn_bootstrap_degraded",
                    "degraded",
                    None,
                )
                result = TurnBootstrap(TurnBootstrapStatus.DEGRADED, None)
            else:
                winner = physical
                if winner is None:
                    validated = _validated_candidate(current, candidate)
                    if validated is None:
                        if turn is None:
                            recorded = _record_bootstrap_state_locked(
                                resolved,
                                event_identity,
                                transaction,
                                "turn_bootstrap_pending",
                                "missing",
                                None,
                            )
                        result = TurnBootstrap(
                            TurnBootstrapStatus.CANDIDATE_REQUIRED,
                            None,
                        )
                    else:
                        initialized = initialize_turn_baseline(
                            resolved,
                            owner,
                            turn_id,
                            True,
                            validated,
                            transaction,
                        )
                        if initialized is BaselineInitialization.CONFLICT:
                            recorded = _record_bootstrap_state_locked(
                                resolved,
                                event_identity,
                                transaction,
                                "turn_bootstrap_degraded",
                                "degraded",
                                None,
                            )
                            result = TurnBootstrap(
                                TurnBootstrapStatus.DEGRADED,
                                None,
                            )
                        else:
                            winner, physical_error = _physical_baseline(
                                resolved,
                                owner,
                                turn_id,
                            )
                            if winner is None or physical_error is not None:
                                recorded = _record_bootstrap_state_locked(
                                    resolved,
                                    event_identity,
                                    transaction,
                                    "turn_bootstrap_degraded",
                                    "degraded",
                                    None,
                                )
                                result = TurnBootstrap(
                                    TurnBootstrapStatus.DEGRADED,
                                    None,
                                )
                            else:
                                result = _finish_recoverable_bootstrap(
                                    resolved,
                                    event_identity,
                                    transaction,
                                    turn,
                                    winner,
                                    current,
                                )
                                recorded = result[1]
                                result = result[0]
                else:
                    result, recorded = _finish_recoverable_bootstrap(
                        resolved,
                        event_identity,
                        transaction,
                        turn,
                        winner,
                        current,
                    )
        except SnapshotStoreError as exc:
            raise ManifestStoreError(str(exc)) from exc
    if recorded is not None and recorded.saved:
        _record_coordination_after_event(str(resolved), recorded.payload)
    return result


def record_turn_event_if_ready(
    root: Path,
    event_payload: Mapping[str, JsonValue],
    expected_baseline_snapshot_id: str,
) -> TurnEventCommit:
    """Commit an invocation only while its physical and ledger baseline still agree."""
    resolved = root.resolve()
    recorded: _RecordedEvent | None = None
    with ledger_transaction(str(resolved)) as transaction:
        try:
            ledger, payload = _load_locked(resolved)
            current = load_workspace_current(resolved)
            ledger = _recover_locked(
                resolved,
                ledger,
                payload,
                current,
                transaction,
            )
            turn = active_turn(ledger, event_payload)
            owner = agent_key(event_payload)
            turn_id = _identity_string(event_payload, "turn_id")
            if turn is None or turn.get("turn_id") != turn_id:
                return TurnEventCommit(TurnEventCommitStatus.STALE_TURN)
            physical, physical_error = _physical_baseline(
                resolved,
                owner,
                turn_id,
            )
            if physical_error is not None or physical is None:
                return TurnEventCommit(TurnEventCommitStatus.DEGRADED)
            if turn is not None:
                status = turn.get("baseline_status")
                if status == "degraded":
                    return TurnEventCommit(TurnEventCommitStatus.DEGRADED)
                if status != "ready":
                    return TurnEventCommit(
                        TurnEventCommitStatus.RETRY,
                        physical.snapshot_id,
                    )
                actual = turn.get("baseline_snapshot_id")
                if not isinstance(actual, str) or physical.snapshot_id != actual:
                    return TurnEventCommit(TurnEventCommitStatus.DEGRADED)
            else:
                actual = physical.snapshot_id
            if actual != expected_baseline_snapshot_id:
                return TurnEventCommit(TurnEventCommitStatus.RETRY, actual)
            recorded = _record_event_locked(event_payload, transaction)
        except SnapshotStoreError:
            return TurnEventCommit(TurnEventCommitStatus.DEGRADED)
    if recorded is None or not recorded.saved:
        return TurnEventCommit(TurnEventCommitStatus.RETRY)
    _record_coordination_after_event(str(resolved), recorded.payload)
    committed_turn = active_turn(recorded.ledger, event_payload)
    if (
        committed_turn is not None
        and committed_turn.get("turn_id") == event_payload.get("turn_id")
        and committed_turn.get("baseline_status") == "ready"
        and committed_turn.get("baseline_snapshot_id")
        == expected_baseline_snapshot_id
    ):
        return TurnEventCommit(
            TurnEventCommitStatus.RECORDED,
            expected_baseline_snapshot_id,
        )
    return TurnEventCommit(TurnEventCommitStatus.RETRY)


def _finish_recoverable_bootstrap(
    root: Path,
    event_identity: Mapping[str, JsonValue],
    transaction: _LedgerTransaction,
    turn: JsonObject | None,
    winner: Snapshot,
    current: Snapshot | None,
) -> tuple[TurnBootstrap, _RecordedEvent | None]:
    initialized = turn is None or turn.get("bootstrap_pending") is True
    recorded = _record_bootstrap_state_locked(
        root,
        event_identity,
        transaction,
        "turn_bootstrap_initialized" if initialized else "turn_bootstrap_recovered",
        "ready",
        winner,
        current,
    )
    if not recorded.saved:
        return TurnBootstrap(TurnBootstrapStatus.DEGRADED, None), recorded
    return TurnBootstrap(TurnBootstrapStatus.READY, winner, not initialized), recorded


def _record_bootstrap_state_locked(
    root: Path,
    event_identity: Mapping[str, JsonValue],
    transaction: _LedgerTransaction,
    event: str,
    baseline_status: str,
    baseline: Snapshot | None,
    current: Snapshot | None = None,
) -> _RecordedEvent:
    ready = baseline_status == "ready" and baseline is not None
    payload: JsonObject = dict(event_identity) | {
        "project_root": str(root),
        "event": event,
        "baseline_status": baseline_status,
        "provenance_incomplete": not ready,
        "provenance_status": (
            ProvenanceStatus.COMPLETE.value
            if ready
            else ProvenanceStatus.INCOMPLETE.value
        ),
        "provenance_status_reason": (
            ProvenanceReason.NONE.value
            if ready
            else (
                ProvenanceReason.TURN_NOT_STARTED.value
                if baseline_status == "missing"
                else ProvenanceReason.BASELINE_STATE_MISMATCH.value
            )
        ),
    }
    if baseline is not None:
        payload["baseline_snapshot_id"] = baseline.snapshot_id
        if current is not None:
            payload["current_snapshot_id"] = current.snapshot_id
        if event == "turn_bootstrap_recovered":
            payload["turn_bootstrap_recovered"] = True
    return _record_event_locked(payload, transaction)


def _physical_baseline(
    root: Path,
    agent: str,
    turn_id: str,
) -> tuple[Snapshot | None, SnapshotStoreError | None]:
    try:
        return load_turn_baseline(root, agent, turn_id), None
    except SnapshotStoreError as exc:
        return None, exc


def _validated_candidate(
    current: Snapshot | None,
    candidate: Snapshot | None,
) -> Snapshot | None:
    if current is None or candidate is None:
        return None
    return current if current.snapshot_id == candidate.snapshot_id else None


def _identity_string(identity: Mapping[str, JsonValue], field: str) -> str:
    value = identity.get(field)
    if not isinstance(value, str) or not value:
        raise SnapshotStoreError(Path("."), f"missing bootstrap identity: {field}")
    return value


def _baseline_path_collision(
    root: Path,
    ledger: JsonObject,
    agent: str,
    turn_id: str,
) -> bool:
    target = turn_baseline_path(root, agent, turn_id)
    active = ledger.get("active_turns")
    if not isinstance(active, dict):
        return False
    for raw_agent, raw_turn in active.items():
        if not isinstance(raw_agent, str) or not isinstance(raw_turn, dict):
            continue
        raw_turn_id = raw_turn.get("turn_id")
        if not isinstance(raw_turn_id, str) or not raw_turn_id:
            continue
        if raw_agent == agent and raw_turn_id == turn_id:
            continue
        if turn_baseline_path(root, raw_agent, raw_turn_id) == target:
            return True
    return False


def _commit_locked(
    root: Path,
    expected_generation: int,
    expected_snapshot_id: str,
    snapshot: Snapshot,
    event_templates: tuple[JsonObject, ...],
    baseline: BaselineUpdate | None,
    required_turn: TurnBinding | None,
    transaction: _LedgerTransaction,
) -> ManifestCommit | None:
    ledger, payload = _load_locked(root)
    current = load_workspace_current(root)
    ledger = _recover_locked(root, ledger, payload, current, transaction)
    if (
        _generation(ledger) != expected_generation
        or _snapshot_id(current) != expected_snapshot_id
    ):
        return None
    if required_turn is not None and not _turn_binding_is_ready(
        root,
        ledger,
        required_turn,
    ):
        return None
    if any(not _event_turn_is_current(root, ledger, event) for event in event_templates):
        return None
    if baseline is not None:
        try:
            physical = load_turn_baseline(root, baseline.agent, baseline.turn_id)
        except SnapshotStoreError:
            return None
        if (
            physical is None
            or physical.snapshot_id != baseline.expected_snapshot_id
        ):
            return None
        active = ledger.get("active_turns")
        raw_turn = active.get(baseline.agent) if isinstance(active, dict) else None
        if isinstance(raw_turn, dict):
            if raw_turn.get("turn_id") != baseline.turn_id:
                return None
            if raw_turn.get("baseline_status") not in {None, "ready"}:
                return None
            if raw_turn.get("baseline_snapshot_id") != baseline.expected_snapshot_id:
                return None
            revisions = raw_turn.get("path_revisions")
            if (
                isinstance(revisions, dict)
                and baseline.candidate_keys.intersection(revisions)
            ):
                return None
    if current == snapshot and (
        baseline is None
        or baseline.snapshot.snapshot_id == baseline.expected_snapshot_id
    ):
        return ManifestCommit(expected_generation, snapshot, (), physical if baseline else None)
    advances_manifest = current != snapshot
    target_generation = expected_generation + 1 if advances_manifest else expected_generation
    events = _prepare_events(ledger, event_templates, target_generation)
    pending: JsonObject = {
        "base_generation": expected_generation,
        "target_generation": target_generation,
        "snapshot_before": expected_snapshot_id,
        "snapshot_after": snapshot.snapshot_id,
        "events": list(events),
    }
    if baseline is not None:
        pending["baseline_agent"] = baseline.agent
        pending["baseline_turn_id"] = baseline.turn_id
        pending["baseline_snapshot_before"] = baseline.expected_snapshot_id
        pending["baseline_snapshot_after"] = baseline.snapshot.snapshot_id
        pending["baseline_candidate_keys"] = sorted(baseline.candidate_keys)
    ledger["manifest_pending"] = pending
    _append_journal(ledger, events)
    _advance_audit_sequence(ledger, events)
    _save_or_raise(root, payload, ledger)
    for event in events:
        append_agent_event(str(root), _event_agent(event), event)
    if advances_manifest:
        save_workspace_current(root, snapshot)
    committed_baseline: Snapshot | None = None
    if baseline is not None:
        outcome = advance_turn_baseline(
            root,
            baseline.agent,
            baseline.turn_id,
            baseline.expected_snapshot_id,
            baseline.snapshot,
            expected_generation,
            transaction,
        )
        if outcome is not BaselineAdvance.COMMITTED:
            raise SnapshotStoreError(
                workspace_current_path(root),
                "turn baseline advance lost its manifest transaction",
            )
        committed_baseline = baseline.snapshot
    committed = _finalize_locked(ledger, pending)
    _save_or_raise(root, payload, ledger)
    for event in committed:
        append_agent_event(str(root), _event_agent(event), event)
    return ManifestCommit(target_generation, snapshot, committed, committed_baseline)


def _load_locked(root: Path) -> tuple[JsonObject, JsonObject]:
    payload: JsonObject = {"project_root": str(root)}
    destination = ledger_path(str(root))
    existed = destination.exists()
    ledger = load_ledger(payload)
    if not existed:
        ledger = default_v2_ledger()
    return ledger, payload


def _recover_locked(
    root: Path,
    ledger: JsonObject,
    payload: JsonObject,
    current: Snapshot | None,
    transaction: _LedgerTransaction,
) -> JsonObject:
    raw_pending = ledger.get("manifest_pending")
    if not isinstance(raw_pending, dict):
        return ledger
    before = raw_pending.get("snapshot_before")
    after = raw_pending.get("snapshot_after")
    current_id = _snapshot_id(current)
    if isinstance(after, str) and current_id == after:
        try:
            _restore_pending_baseline(root, raw_pending, current, transaction)
        except SnapshotStoreError:
            _degrade_pending_baseline(ledger, raw_pending)
        committed = _committed_events(raw_pending)
        for event in committed:
            append_agent_event(str(root), _event_agent(event), event)
        _ = _finalize_locked(ledger, raw_pending)
        _save_or_raise(root, payload, ledger)
        return ledger
    if isinstance(before, str) and current_id == before:
        _ = ledger.pop("manifest_pending", None)
        _save_or_raise(root, payload, ledger)
        return ledger
    raise SnapshotStoreError(
        workspace_current_path(root),
        "does not match the pending manifest transition",
    )


def _restore_pending_baseline(
    root: Path,
    pending: JsonObject,
    current: Snapshot | None,
    transaction: _LedgerTransaction,
) -> None:
    agent = pending.get("baseline_agent")
    turn_id = pending.get("baseline_turn_id")
    if (
        current is None
        or not isinstance(agent, str)
        or not agent
        or not isinstance(turn_id, str)
        or not turn_id
    ):
        return
    expected = pending.get("baseline_snapshot_before")
    target = pending.get("baseline_snapshot_after")
    candidate_keys = pending.get("baseline_candidate_keys")
    if not isinstance(expected, str) or not isinstance(target, str):
        _restore_legacy_pending_baseline(
            root,
            pending,
            current,
            agent,
            turn_id,
            transaction,
        )
        return
    try:
        baseline = load_turn_baseline(root, agent, turn_id)
    except SnapshotStoreError:
        baseline = None
    if baseline is not None and baseline.snapshot_id == target:
        return
    if baseline is None or baseline.snapshot_id != expected:
        raise SnapshotStoreError(
            workspace_current_path(root),
            "does not match the pending baseline transition",
        )
    keys = frozenset(
        item for item in candidate_keys if isinstance(item, str)
    ) if isinstance(candidate_keys, list) else frozenset()
    merged = merge_turn_baseline(baseline, current, keys)
    if merged.snapshot_id != target:
        raise SnapshotStoreError(
            workspace_current_path(root),
            "cannot reconstruct the pending baseline transition",
        )
    base_generation = pending.get("base_generation")
    if not isinstance(base_generation, int) or isinstance(base_generation, bool):
        raise SnapshotStoreError(
            workspace_current_path(root),
            "pending baseline generation is invalid",
        )
    outcome = advance_turn_baseline(
        root,
        agent,
        turn_id,
        expected,
        merged,
        base_generation,
        transaction,
    )
    if outcome is not BaselineAdvance.COMMITTED:
        raise SnapshotStoreError(
            workspace_current_path(root),
            "could not recover the pending baseline transition",
        )


def _degrade_pending_baseline(ledger: JsonObject, pending: JsonObject) -> None:
    agent = pending.get("baseline_agent")
    turn_id = pending.get("baseline_turn_id")
    active = ledger.get("active_turns")
    if not isinstance(agent, str) or not isinstance(turn_id, str) or not isinstance(active, dict):
        return
    raw_turn = active.get(agent)
    if not isinstance(raw_turn, dict) or raw_turn.get("turn_id") != turn_id:
        return
    raw_turn["baseline_status"] = "degraded"
    raw_turn["provenance_incomplete"] = True
    raw_turn["provenance_status"] = ProvenanceStatus.INCOMPLETE.value
    raw_turn["provenance_status_reason"] = ProvenanceReason.BASELINE_STATE_MISMATCH.value


def _restore_legacy_pending_baseline(
    root: Path,
    pending: JsonObject,
    current: Snapshot,
    agent: str,
    turn_id: str,
    transaction: _LedgerTransaction,
) -> None:
    try:
        physical = load_turn_baseline(root, agent, turn_id)
    except SnapshotStoreError as exc:
        raise SnapshotStoreError(
            workspace_current_path(root),
            "legacy pending baseline is unreadable",
        ) from exc
    if physical is None:
        outcome = initialize_turn_baseline(
            root,
            agent,
            turn_id,
            True,
            current,
            transaction,
        )
        if outcome is BaselineInitialization.CONFLICT:
            raise SnapshotStoreError(
                workspace_current_path(root),
                "could not restore the legacy pending baseline",
            )
        return
    base_generation = pending.get("base_generation")
    if not isinstance(base_generation, int) or isinstance(base_generation, bool):
        raise SnapshotStoreError(
            workspace_current_path(root),
            "legacy pending baseline generation is invalid",
        )
    outcome = advance_turn_baseline(
        root,
        agent,
        turn_id,
        physical.snapshot_id,
        current,
        base_generation,
        transaction,
    )
    if outcome is not BaselineAdvance.COMMITTED:
        raise SnapshotStoreError(
            workspace_current_path(root),
            "could not advance the legacy pending baseline",
        )


def _prepare_events(
    ledger: JsonObject,
    templates: tuple[JsonObject, ...],
    target_generation: int,
) -> tuple[JsonObject, ...]:
    start_seq = sequence_value(ledger.get("event_seq"))
    return tuple(
        template
        | {
            "seq": start_seq + index,
            "manifest_generation": target_generation,
            "commit_state": "uncommitted",
        }
        for index, template in enumerate(templates, start=1)
    )


def _advance_audit_sequence(
    ledger: JsonObject,
    events: tuple[JsonObject, ...],
) -> None:
    for event in events:
        if ledger.get("schema_version") == 2:
            _ = apply_v2_event(ledger, event)
        else:
            ledger["event_seq"] = max(
                sequence_value(ledger.get("event_seq")),
                sequence_value(event.get("seq")),
            )


def _finalize_locked(
    ledger: JsonObject,
    pending: JsonObject,
) -> tuple[JsonObject, ...]:
    target = pending.get("target_generation")
    generation = (
        target if isinstance(target, int) and not isinstance(target, bool) else 0
    )
    committed = _committed_events(pending)
    for event in committed:
        if ledger.get("schema_version") == 2:
            _ = apply_v2_event(ledger, event)
        else:
            _ = apply_v1_event(ledger, event)
    ledger["manifest_generation"] = generation
    after = pending.get("snapshot_after")
    if isinstance(after, str) and after:
        ledger["manifest_snapshot_id"] = after
    baseline_agent = pending.get("baseline_agent")
    baseline_turn_id = pending.get("baseline_turn_id")
    baseline_after = pending.get("baseline_snapshot_after")
    active = ledger.get("active_turns")
    if (
        isinstance(active, dict)
        and isinstance(baseline_agent, str)
        and isinstance(baseline_turn_id, str)
        and isinstance(baseline_after, str)
    ):
        raw_turn = active.get(baseline_agent)
        if (
            isinstance(raw_turn, dict)
            and raw_turn.get("turn_id") == baseline_turn_id
            and raw_turn.get("baseline_status") != "degraded"
        ):
            raw_turn["baseline_snapshot_id"] = baseline_after
            raw_turn["baseline_status"] = "ready"
    _append_journal(ledger, committed)
    _ = ledger.pop("manifest_pending", None)
    return committed


def _committed_events(pending: JsonObject) -> tuple[JsonObject, ...]:
    return tuple(
        event | {"commit_state": "committed"}
        for event in _events(pending.get("events"))
    )


def _append_journal(
    ledger: JsonObject,
    events: tuple[JsonObject, ...],
) -> None:
    current = ledger.get("manifest_event_journal")
    journal = (
        [item for item in current if isinstance(item, dict)]
        if isinstance(current, list)
        else []
    )
    journal.extend(events)
    ledger["manifest_event_journal"] = journal[-JOURNAL_LIMIT:]


def _events(value: JsonValue | None) -> tuple[JsonObject, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _save_or_raise(root: Path, payload: JsonObject, ledger: JsonObject) -> None:
    if not save_ledger(payload, ledger):
        raise SnapshotStoreError(
            ledger_path(str(root)),
            "could not persist manifest transaction",
        )


def _snapshot_id(snapshot: Snapshot | None) -> str:
    return snapshot.snapshot_id if snapshot is not None else UNAVAILABLE_SNAPSHOT


def _generation(ledger: JsonObject) -> int:
    return sequence_value(ledger.get("manifest_generation"))


def _event_agent(event: JsonObject) -> str:
    agent = event.get("agent")
    return agent if isinstance(agent, str) else ""


def _event_turn_is_current(root: Path, ledger: JsonObject, event: JsonObject) -> bool:
    turn = active_turn(ledger, event)
    if turn is None:
        return True
    turn_id = event.get("turn_id")
    if not isinstance(turn_id, str) or turn.get("turn_id") != turn_id:
        return False
    status = turn.get("baseline_status")
    if status not in {None, "ready"}:
        return False
    if status is None:
        return True
    expected = turn.get("baseline_snapshot_id")
    if not isinstance(expected, str):
        return False
    if expected == UNAVAILABLE_SNAPSHOT:
        return True
    try:
        physical = load_turn_baseline(root, agent_key(event), turn_id)
    except SnapshotStoreError:
        return False
    return physical is not None and physical.snapshot_id == expected


def _turn_binding_is_ready(
    root: Path,
    ledger: JsonObject,
    binding: TurnBinding,
) -> bool:
    active = ledger.get("active_turns")
    turn = active.get(binding.agent) if isinstance(active, dict) else None
    if (
        not isinstance(turn, dict)
        or turn.get("turn_id") != binding.turn_id
        or turn.get("baseline_status") != "ready"
    ):
        return False
    expected = turn.get("baseline_snapshot_id")
    if not isinstance(expected, str) or expected == UNAVAILABLE_SNAPSHOT:
        return False
    try:
        physical = load_turn_baseline(root, binding.agent, binding.turn_id)
    except SnapshotStoreError:
        return False
    return physical is not None and physical.snapshot_id == expected


def merge_turn_baseline(
    baseline: Snapshot,
    current: Snapshot,
    candidate_keys: frozenset[str],
) -> Snapshot:
    if (
        baseline.root.resolve() != current.root.resolve()
        or baseline.is_casefolded != current.is_casefolded
        or baseline.scope_policy_id != current.scope_policy_id
    ):
        raise SnapshotStoreError(
            workspace_current_path(baseline.root),
            "candidate baseline merge policy does not match",
        )
    entries = {
        canonical_manifest_key(entry.path, baseline.is_casefolded): entry
        for entry in baseline.entries
    }
    current_entries = {
        canonical_manifest_key(entry.path, baseline.is_casefolded): entry
        for entry in current.entries
    }
    for key in candidate_keys:
        candidate = current_entries.get(key)
        if key not in entries and candidate is not None:
            entries[key] = candidate
    reparses = {
        canonical_manifest_key(entry.path, baseline.is_casefolded): entry
        for entry in baseline.reparse_observations
    }
    current_reparses = {
        canonical_manifest_key(entry.path, baseline.is_casefolded): entry
        for entry in current.reparse_observations
    }
    for key in candidate_keys:
        candidate = current_reparses.get(key)
        if key not in reparses and candidate is not None:
            reparses[key] = candidate
    ordered_entries = tuple(
        sorted(entries.values(), key=lambda item: (item.canonical_key, item.path))
    )
    ordered_reparses = tuple(
        sorted(reparses.values(), key=lambda item: (item.canonical_key, item.path))
    )
    return replace(
        baseline,
        entries=ordered_entries,
        reparse_observations=ordered_reparses,
        snapshot_id=snapshot_id_for(ordered_entries, baseline.exclusions),
    )
