from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .agent_log import ledger_transaction
from .provenance import (
    calculate_net_delta,
)
from .provenance_lifecycle_types import (
    Invocation,
    LifecycleState,
    ObservationInput,
    ObservationResult,
    ObservedChange,
    TurnState,
)
from .provenance_observation import pending_change_ids, record_deltas
from .provenance_store import (
    SnapshotStoreError,
    delete_turn_baseline,
    load_workspace_current,
    save_turn_baseline_from_current,
    save_workspace_current,
    turn_baseline_path,
    workspace_current_path,
)
from .provenance_turn_resume import (
    MissingTurnBaselineError,
    TurnBootstrapError,
    load_resumed_turn,
)
from .provenance_lifecycle_start import can_fast_start, candidate_paths as candidate_paths_for_root
from .provenance_lifecycle_scope import prime_candidate_scope, scan_snapshot
from .project_root import is_user_home_root
from .provenance_types import ProvenanceReason, ProvenanceStatus, Snapshot


class ProvenanceLifecycle:
    """Coordinates in-process provenance observations while snapshots persist independently."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._home_root_unsupported = is_user_home_root(self._root)
        if self._home_root_unsupported:
            self._state = LifecycleState(str(self._root))
            return
        try:
            current = load_workspace_current(self._root)
        except SnapshotStoreError:
            current = None
            self._state = LifecycleState(str(self._root))
            self._state.incomplete = True
            self._state.incomplete_reason = ProvenanceReason.STORE_READ_ERROR
        else:
            self._state = LifecycleState(str(self._root), current)

    @property
    def workspace_current_path(self) -> Path:
        return workspace_current_path(self._root)

    @property
    def observed_file_count(self) -> int:
        return len(self._state.current.entries) if self._state.current is not None else 0

    @property
    def changes(self) -> tuple[ObservedChange, ...]:
        return tuple(sorted(self._state.changes.values(), key=lambda change: change.change_id))

    @property
    def active_turns(self) -> tuple[TurnState, ...]:
        return tuple(sorted(self._state.turns.values(), key=lambda turn: (turn.agent, turn.turn_id)))

    def turn_baseline_path(self, agent: str, turn_id: str) -> Path:
        return turn_baseline_path(self._root, agent, turn_id)

    def start_turn(
        self,
        agent: str,
        turn_id: str,
        mutation_capable: bool = False,
    ) -> ObservationResult:
        if self._home_root_unsupported:
            return ObservationResult(
                None,
                (),
                (),
                False,
                False,
                0,
                False,
                False,
                ProvenanceStatus.UNSUPPORTED,
                ProvenanceReason.HOME_ROOT,
            )
        if self._state.incomplete_reason is ProvenanceReason.STORE_READ_ERROR:
            return self._mark_incomplete(
                self._result(None, (), True, 0),
                ProvenanceReason.STORE_READ_ERROR,
            )
        result = self._observe(
            "",
            "external",
            frozenset(),
            not can_fast_start(
                self._state.current,
                self._state.current_is_stop_full,
                self._state.incomplete,
                self._root,
            ),
        )
        if result.status is not ProvenanceStatus.COMPLETE or result.snapshot is None:
            return result
        turn = TurnState(agent, turn_id, result.snapshot, self._state.event_seq, mutation_capable)
        try:
            save_turn_baseline_from_current(self._root, agent, turn_id, turn.baseline)
        except SnapshotStoreError:
            return self._mark_incomplete(result, ProvenanceReason.STORE_WRITE_ERROR)
        self._state.turns[(agent, turn_id)] = turn
        return self._turn_result(result, turn, False)

    def begin_invocation(
        self,
        agent: str,
        turn_id: str,
        invocation_id: str,
        candidate_paths: tuple[str, ...],
        prime_candidates: bool = True,
    ) -> Invocation:
        candidates = candidate_paths_for_root(self._root, candidate_paths)
        if prime_candidates and not prime_candidate_scope(self._root, self._state, agent, turn_id, candidates):
            self._state.incomplete = True
        snapshot_id = self._state.current.snapshot_id if self._state.current is not None else ""
        return Invocation(
            invocation_id,
            agent,
            turn_id,
            self._state.event_seq,
            snapshot_id,
            candidates,
        )

    def resume_turn(
        self,
        agent: str,
        turn_id: str,
        mutation_capable: bool = False,
        *,
        allow_full_bootstrap: bool = False,
    ) -> None:
        if (agent, turn_id) in self._state.turns:
            return
        try:
            turn = load_resumed_turn(
                self._root, agent, turn_id, self._state.event_seq, mutation_capable
            )
        except MissingTurnBaselineError:
            if not allow_full_bootstrap:
                raise
            current = self._state.current
            if current is None:
                result = self.start_turn(agent, turn_id, mutation_capable)
                if (
                    result.status is ProvenanceStatus.COMPLETE
                    and not result.incomplete
                ):
                    return
                raise TurnBootstrapError(
                    result.status,
                    result.status_reason,
                    result.incomplete,
                )
            _ = save_turn_baseline_from_current(
                self._root, agent, turn_id, current
            )
            turn = TurnState(
                agent,
                turn_id,
                current,
                self._state.event_seq,
                mutation_capable,
            )
        self._state.turns[(agent, turn_id)] = turn

    def post_tool(self, invocation: Invocation, source: str = "external") -> ObservationResult:
        turn = self._state.turns[(invocation.agent, invocation.turn_id)]
        result = self._observe(invocation.agent, source, invocation.candidate_paths, False)
        if result.status is ProvenanceStatus.COMPLETE and self._state.current is not None:
            record_deltas(
                self._state,
                ObservationInput(
                    calculate_net_delta(turn.baseline, self._state.current),
                    invocation.agent,
                    source,
                ),
            )
        return self._turn_result(result, turn, False)

    def reconcile_turn(self, agent: str, turn_id: str) -> ObservationResult:
        turn = self._state.turns[(agent, turn_id)]
        result = self._observe(agent, "external", frozenset(), True)
        if result.status is ProvenanceStatus.COMPLETE and self._state.current is not None:
            finalized = replace(
                self._state.current,
                full_reconciled_at=datetime.now(UTC).isoformat(),
            )
            try:
                save_workspace_current(self._root, finalized)
            except SnapshotStoreError:
                result = self._mark_incomplete(
                    result, ProvenanceReason.STORE_WRITE_ERROR
                )
            else:
                self._state.current = finalized
                self._state.current_is_stop_full = True
                result = replace(result, snapshot=finalized)
        if not result.incomplete and self._state.current is not None:
            record_deltas(
                self._state,
                ObservationInput(
                    calculate_net_delta(turn.baseline, self._state.current),
                    agent,
                    "external",
                ),
            )
        return self._turn_result(result, turn, False)

    def finish_turn(self, agent: str, turn_id: str) -> ObservationResult:
        turn = self._state.turns[(agent, turn_id)]
        result = self.reconcile_turn(agent, turn_id)
        reserved = result.incomplete and turn.mutation_capable
        if reserved:
            self._state.stop_cap_reservations.add((agent, turn_id))
        try:
            delete_turn_baseline(self._root, agent, turn_id)
        except SnapshotStoreError:
            result = self._mark_incomplete(result, ProvenanceReason.STORE_WRITE_ERROR)
            reserved = turn.mutation_capable
        self._state.turns.pop((agent, turn_id), None)
        return replace(result, stop_cap_reserved=reserved)

    def _scan(
        self,
        previous: Snapshot | None,
        forced_paths: frozenset[str],
        full_scan: bool,
    ) -> Snapshot:
        return scan_snapshot(self._root, previous, forced_paths, full_scan)

    def _observe(
        self,
        agent: str,
        source: str,
        forced_paths: frozenset[str],
        full_scan: bool,
    ) -> ObservationResult:
        generation, previous = self._generation_current()
        first = self._scan(previous, forced_paths, full_scan)
        if first.status is ProvenanceStatus.SCOPE_TOO_LARGE:
            return self._mark_scope_too_large(self._result(first, (), full_scan, 0))
        if first.incomplete:
            reason = (
                first.status_reason
                if first.status_reason is not ProvenanceReason.NONE
                else ProvenanceReason.OBSERVATION_ERROR
            )
            return self._mark_incomplete(
                self._result(first, (), full_scan, 0),
                reason,
            )
        committed = self._commit_if_current(generation, first, agent, source, full_scan)
        if committed is not None:
            return committed
        rebase_generation, rebase_previous = self._generation_current()
        rebased = self._scan(rebase_previous, forced_paths, full_scan)
        if rebased.status is ProvenanceStatus.SCOPE_TOO_LARGE:
            return self._mark_scope_too_large(self._result(rebased, (), full_scan, 1))
        if rebased.incomplete:
            return self._mark_incomplete(self._result(rebased, (), full_scan, 1))
        committed = self._commit_if_current(rebase_generation, rebased, agent, source, full_scan)
        if committed is not None:
            return replace(committed, rebase_count=1)
        return self._mark_incomplete(
            self._result(None, (), full_scan, 1),
            ProvenanceReason.OBSERVATION_ERROR,
        )

    def _generation_current(self) -> tuple[int, Snapshot | None]:
        with ledger_transaction(str(self._root)):
            return self._state.generation, self._state.current

    def _commit_if_current(
        self,
        generation: int,
        snapshot: Snapshot,
        agent: str,
        source: str,
        full_scan: bool,
    ) -> ObservationResult | None:
        with ledger_transaction(str(self._root)):
            if self._state.generation != generation:
                return None
            before = self._state.current
            unchanged = before if before is not None and before.snapshot_id == snapshot.snapshot_id else None
            deltas = () if before is None or unchanged is not None else calculate_net_delta(before, snapshot)
            changes = record_deltas(self._state, ObservationInput(deltas, agent, source))
            reconciled_at = unchanged.full_reconciled_at if unchanged is not None else None
            snapshot = replace(snapshot, full_reconciled_at=reconciled_at)
            if unchanged is None:
                try:
                    save_workspace_current(self._root, snapshot)
                except SnapshotStoreError:
                    return self._mark_incomplete(
                        self._result(snapshot, changes, full_scan, 0),
                        ProvenanceReason.STORE_WRITE_ERROR,
                    )
            elif unchanged.full_reconciled_at is not None:
                snapshot = unchanged
            self._state.current = snapshot
            self._state.generation += 1
            self._state.incomplete = False
            self._state.incomplete_reason = ProvenanceReason.NONE
            self._state.current_is_stop_full = reconciled_at is not None
            return self._result(snapshot, changes, full_scan, 0)

    def _turn_result(self, result: ObservationResult, turn: TurnState, stop_cap_reserved: bool) -> ObservationResult:
        pending = pending_change_ids(self._state, turn)
        return replace(
            result,
            pending_change_ids=pending,
            clean_claim=(
                result.status is ProvenanceStatus.COMPLETE
                and not result.incomplete
                and not pending
            ),
            stop_cap_reserved=stop_cap_reserved,
        )

    def _mark_incomplete(
        self,
        result: ObservationResult,
        reason: ProvenanceReason = ProvenanceReason.OBSERVATION_ERROR,
    ) -> ObservationResult:
        self._state.incomplete = True
        self._state.incomplete_reason = reason
        return replace(
            result,
            incomplete=True,
            clean_claim=False,
            status=ProvenanceStatus.INCOMPLETE,
            status_reason=reason,
        )

    def _mark_scope_too_large(self, result: ObservationResult) -> ObservationResult:
        return replace(
            result,
            incomplete=False,
            clean_claim=False,
            status=ProvenanceStatus.SCOPE_TOO_LARGE,
        )

    @staticmethod
    def _result(
        snapshot: Snapshot | None,
        changes: tuple[ObservedChange, ...],
        full_scan: bool,
        rebase_count: int,
    ) -> ObservationResult:
        status = snapshot.status if snapshot is not None else ProvenanceStatus.INCOMPLETE
        reason = (
            snapshot.status_reason
            if snapshot is not None
            else ProvenanceReason.SNAPSHOT_UNAVAILABLE
        )
        return ObservationResult(
            snapshot,
            changes,
            (),
            snapshot.incomplete if snapshot is not None else True,
            full_scan,
            rebase_count,
            False,
            False,
            status,
            reason,
        )
