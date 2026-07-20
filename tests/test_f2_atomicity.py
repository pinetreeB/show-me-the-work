from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

import core.adapter_observation as adapter_observation
import core.agent_log as agent_log
import core.provenance_lifecycle as provenance_lifecycle_module
import core.provenance_manifest as provenance_manifest
from core.adapter_observation import CanonicalInvocation, begin_invocation, observe_post_tool
from core.agent_log import ledger_transaction, load_agent_events
from core.ledger import (
    _record_event_locked,
    load_ledger,
    record_event,
    record_event_if_current_turn,
    save_ledger,
)
from core.ledger_schema import LedgerSchemaError, validate_v2_ledger
from core.ledger_v2 import default_v2_ledger
from core.provenance import calculate_net_delta, snapshot_workspace
from core.provenance_lifecycle import ProvenanceLifecycle
from core.provenance_manifest import (
    BaselineUpdate,
    commit_manifest,
    load_manifest_view,
    merge_turn_baseline,
)
from core.provenance_store import (
    BaselineInitialization,
    SnapshotStoreError,
    initialize_turn_baseline,
    load_turn_baseline,
    save_turn_baseline,
    turn_baseline_path,
)
from core.provenance_types import ProvenanceReason, ProvenanceStatus, Snapshot
from core.verification_covers import active_turn
from core.verify_state import evaluate_stop, evaluate_without_io


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tests" / "support" / "f2_baseline_worker.py"
AGENT_KEY = "host:session:caller"
TURN_ID = "turn"


def _invocation(
    invocation_id: str = "edit-1",
    candidates: tuple[str, ...] = (),
) -> CanonicalInvocation:
    return CanonicalInvocation(
        "host",
        "caller",
        "session",
        TURN_ID,
        invocation_id,
        "pre_tool",
        "edit",
        candidates,
        "",
        True,
        "",
    )


def _prompt_payload(root: Path, baseline_id: str, *, missing: bool = False) -> dict[str, object]:
    return {
        "project_root": str(root),
        "event": "prompt",
        "host": "host",
        "session_id": "session",
        "agent": "caller",
        "turn_id": TURN_ID,
        "attribution": "exact",
        "prompt": "edit app.py",
        "baseline_snapshot_id": "snapshot:unavailable" if missing else baseline_id,
        "current_snapshot_id": "snapshot:unavailable" if missing else baseline_id,
        "provenance_incomplete": missing,
        "provenance_status": "incomplete" if missing else "complete",
        "provenance_status_reason": "observation_error" if missing else "",
    }


def _seed_active_turn(root: Path) -> tuple[ProvenanceLifecycle, Snapshot]:
    (root / "app.py").write_text("before", encoding="utf-8")
    lifecycle = ProvenanceLifecycle(root)
    result = lifecycle.start_turn(AGENT_KEY, TURN_ID, True)
    baseline = load_turn_baseline(root, AGENT_KEY, TURN_ID)
    assert result.snapshot is not None and baseline is not None
    _ = record_event(_prompt_payload(root, baseline.snapshot_id))
    return lifecycle, baseline


def _baseline_only_candidate(root: Path) -> tuple[ProvenanceLifecycle, Snapshot]:
    _caller, baseline = _seed_active_turn(root)
    (root / "late.py").write_text("already-current", encoding="utf-8")
    peer = ProvenanceLifecycle(root)
    result = peer.start_turn("peer", "peer-turn")
    assert result.snapshot is not None and any(
        entry.path == "late.py" for entry in result.snapshot.entries
    )
    caller = ProvenanceLifecycle(root)
    caller.resume_turn(AGENT_KEY, TURN_ID, True)
    return caller, baseline


def _entry_digest(snapshot: Snapshot, path: str) -> str:
    return next(entry.digest for entry in snapshot.entries if entry.path == path)


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 20
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"worker did not create {path.name}")
        time.sleep(0.01)


def test_two_process_initialization_has_one_first_valid_winner(tmp_path: Path) -> None:
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]),
        "FABLE_LITE_TEST_LOCK_WAIT_SECONDS": "45",
    }
    processes = [
        subprocess.Popen(
            [sys.executable, str(WORKER), str(tmp_path), label],
            cwd=ROOT,
            env=environment,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for label in ("a", "b")
    ]
    try:
        _wait_for(tmp_path / "ready-a")
        _wait_for(tmp_path / "ready-b")
        (tmp_path / "go").write_text("go", encoding="ascii")
        outputs = [process.communicate(timeout=30) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                _ = process.wait(timeout=10)
    assert [process.returncode for process in processes] == [0, 0], outputs
    results = [json.loads(stdout) for stdout, _stderr in outputs]
    assert sorted(item["outcome"] for item in results) == ["created", "existing"]
    assert len({item["winner"] for item in results}) == 1
    physical = load_turn_baseline(tmp_path, "host:session:agent", TURN_ID)
    assert physical is not None
    assert physical.snapshot_id == results[0]["winner"]


def test_safe_key_collision_never_overwrites_the_first_identity(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("before", encoding="utf-8")
    candidate = snapshot_workspace(tmp_path)
    with ledger_transaction(str(tmp_path)) as transaction:
        first = initialize_turn_baseline(
            tmp_path, "agent:a", TURN_ID, True, candidate, transaction
        )
    with ledger_transaction(str(tmp_path)) as transaction:
        second = initialize_turn_baseline(
            tmp_path, "agent-a", TURN_ID, True, candidate, transaction
        )
    assert first is BaselineInitialization.CREATED
    assert second is BaselineInitialization.CONFLICT
    assert load_turn_baseline(tmp_path, "agent:a", TURN_ID) == candidate


def test_load_turn_baseline_parses_json_once(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("before", encoding="utf-8")
    candidate = snapshot_workspace(tmp_path)
    save_turn_baseline(tmp_path, "agent:a", TURN_ID, candidate)

    with patch("core.provenance_store.json.loads", wraps=json.loads) as loads:
        assert load_turn_baseline(tmp_path, "agent:a", TURN_ID) == candidate

    assert loads.call_count == 1


@pytest.mark.parametrize("missing", ["baseline_agent", "baseline_turn_id"])
def test_partial_baseline_identity_fails_closed(
    tmp_path: Path,
    missing: str,
) -> None:
    (tmp_path / "app.py").write_text("before", encoding="utf-8")
    candidate = snapshot_workspace(tmp_path)
    save_turn_baseline(tmp_path, "agent:a", TURN_ID, candidate)
    path = turn_baseline_path(tmp_path, "agent:a", TURN_ID)
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw[missing]
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(SnapshotStoreError, match="safe-key collision"):
        _ = load_turn_baseline(tmp_path, "agent:a", TURN_ID)


def test_locked_event_token_rejects_wrong_root_thread_and_reuse(tmp_path: Path) -> None:
    other = tmp_path / "other"
    failures: list[str] = []
    with ledger_transaction(str(tmp_path)) as transaction:
        with pytest.raises(RuntimeError, match="different project root"):
            _ = _record_event_locked(
                {"project_root": str(other), "event": "scope_warning"},
                transaction,
            )

        def cross_thread() -> None:
            try:
                transaction.assert_active_for(str(tmp_path))
            except RuntimeError as exc:
                failures.append(str(exc))

        thread = threading.Thread(target=cross_thread)
        thread.start()
        thread.join(timeout=10)
    assert failures and "thread boundary" in failures[0]
    with pytest.raises(RuntimeError, match="no longer active"):
        transaction.assert_active_for(str(tmp_path))


def test_fresh_dead_owner_is_reclaimable_without_age_grace(tmp_path: Path) -> None:
    lock = tmp_path / "ledger.lock"
    owner = "2147483647:dead-owner"
    lock.write_text(owner, encoding="ascii")

    assert agent_log._stale_record(lock) == owner


def test_malformed_owner_uses_age_grace(tmp_path: Path) -> None:
    lock = tmp_path / "ledger.lock"
    lock.write_bytes(b"\xffpartial-owner")

    assert agent_log._stale_record(lock) is None
    os.utime(lock, (0, 0))
    stale = agent_log._stale_record(lock)
    assert isinstance(stale, str) and stale.startswith("malformed:")


def test_missing_ledger_adopts_physical_winner_without_regressing_current(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("a", encoding="utf-8")
    caller = ProvenanceLifecycle(tmp_path)
    started = caller.start_turn(AGENT_KEY, TURN_ID, True)
    baseline = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    assert started.snapshot is not None and baseline is not None
    _ = record_event(_prompt_payload(tmp_path, baseline.snapshot_id, missing=True))
    (tmp_path / "app.py").write_text("b", encoding="utf-8")
    advanced = ProvenanceLifecycle(tmp_path).start_turn("peer", "peer-turn")
    assert advanced.snapshot is not None

    report = begin_invocation(tmp_path, _invocation())

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    physical = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    assert report.incomplete is False
    assert physical is not None and physical.snapshot_id == baseline.snapshot_id
    assert turn is not None
    assert turn["baseline_status"] == "ready"
    assert turn["baseline_snapshot_id"] == baseline.snapshot_id
    assert turn["current_snapshot_id"] == advanced.snapshot.snapshot_id
    events = turn["invocations"]
    assert isinstance(events, dict) and "edit-1" in events


def test_missing_without_baseline_ignores_partial_temp_and_bootstraps(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("stable", encoding="utf-8")
    destination = turn_baseline_path(tmp_path, AGENT_KEY, TURN_ID)
    destination.parent.mkdir(parents=True)
    (destination.parent / "snapshot-crash.tmp").write_text("{partial", encoding="utf-8")
    _ = record_event(_prompt_payload(tmp_path, "snapshot:unavailable", missing=True))

    report = begin_invocation(tmp_path, _invocation())

    physical = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    assert report.incomplete is False
    assert physical is not None
    assert turn is not None and turn["baseline_status"] == "ready"
    assert turn["baseline_snapshot_id"] == physical.snapshot_id


def test_invocation_without_prompt_bootstraps_and_records_ready_turn(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("stable", encoding="utf-8")

    report = begin_invocation(tmp_path, _invocation("without-prompt"))

    physical = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    assert report.incomplete is False
    assert physical is not None
    assert turn is not None
    assert turn["baseline_status"] == "ready"
    assert turn["baseline_snapshot_id"] == physical.snapshot_id
    assert "without-prompt" in turn["invocations"]


def test_missing_ledger_adopts_metadata_free_legacy_baseline(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("legacy", encoding="utf-8")
    lifecycle = ProvenanceLifecycle(tmp_path)
    started = lifecycle.start_turn(AGENT_KEY, TURN_ID, True)
    baseline = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    assert started.snapshot is not None and baseline is not None
    baseline_path = turn_baseline_path(tmp_path, AGENT_KEY, TURN_ID)
    raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    del raw["baseline_agent"]
    del raw["baseline_turn_id"]
    baseline_path.write_text(json.dumps(raw), encoding="utf-8")
    _ = record_event(_prompt_payload(tmp_path, baseline.snapshot_id, missing=True))

    report = begin_invocation(tmp_path, _invocation("legacy-residue"))

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    assert report.incomplete is False
    assert turn is not None and turn["baseline_status"] == "ready"
    assert turn["baseline_snapshot_id"] == baseline.snapshot_id


@pytest.mark.parametrize("damage", ["missing", "corrupt", "invalid_utf", "mismatch"])
def test_ready_baseline_damage_becomes_sticky_degraded(
    tmp_path: Path,
    damage: str,
) -> None:
    _lifecycle, baseline = _seed_active_turn(tmp_path)
    path = turn_baseline_path(tmp_path, AGENT_KEY, TURN_ID)
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_text("{not-json", encoding="utf-8")
    elif damage == "invalid_utf":
        path.write_bytes(b"\xff\xfe")
    else:
        (tmp_path / "app.py").write_text("different", encoding="utf-8")
        save_turn_baseline(tmp_path, AGENT_KEY, TURN_ID, snapshot_workspace(tmp_path))

    report = begin_invocation(tmp_path, _invocation())
    _ = record_event(
        _prompt_payload(tmp_path, baseline.snapshot_id)
        | {
            "event": "turn_started",
            "provenance_incomplete": False,
            "provenance_status": "complete",
            "provenance_status_reason": "",
            "baseline_status": "ready",
        }
    )
    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))

    assert report.incomplete is True
    assert report.status_reason is ProvenanceReason.BASELINE_STATE_MISMATCH
    assert turn is not None
    assert turn["baseline_status"] == "degraded"
    assert turn["baseline_snapshot_id"] == baseline.snapshot_id
    assert turn["provenance_incomplete"] is True
    assert turn["provenance_status"] == ProvenanceStatus.INCOMPLETE.value
    assert turn["provenance_status_reason"] == "baseline_state_mismatch"
    assert turn["provenance_mutation_capable"] is True
    assert evaluate_without_io(ledger, _prompt_payload(tmp_path, "unused"))["decision"] == "block"


def test_candidate_advance_preserves_prior_baseline_bytes_and_pending_delta(
    tmp_path: Path,
) -> None:
    lifecycle, baseline = _seed_active_turn(tmp_path)
    invocation = lifecycle.begin_invocation(AGENT_KEY, TURN_ID, "write-app", ("app.py",))
    (tmp_path / "app.py").write_text("after", encoding="utf-8")
    observed = lifecycle.post_tool(invocation, "edit")
    assert observed.pending_change_ids
    (tmp_path / "late.py").write_text("primed", encoding="utf-8")

    _ = lifecycle.begin_invocation(AGENT_KEY, TURN_ID, "write-late", ("late.py",))

    physical = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    current = lifecycle.current_snapshot
    assert physical is not None and current is not None
    assert _entry_digest(physical, "app.py") == _entry_digest(baseline, "app.py")
    assert any(entry.path == "late.py" for entry in physical.entries)
    assert [delta.path for delta in calculate_net_delta(physical, current)] == ["app.py"]
    assert observed.pending_change_ids[0] in lifecycle._state.changes


def test_candidate_reprime_does_not_absorb_a_prior_created_path(tmp_path: Path) -> None:
    lifecycle, baseline = _seed_active_turn(tmp_path)
    first = lifecycle.begin_invocation(
        AGENT_KEY,
        TURN_ID,
        "create",
        ("new.py",),
        event_agent="caller",
        host="host",
        session_id="session",
    )
    (tmp_path / "new.py").write_text("created", encoding="utf-8")
    observed = lifecycle.post_tool(first, "edit")
    assert observed.pending_change_ids

    resumed = ProvenanceLifecycle(tmp_path)
    resumed.resume_turn(AGENT_KEY, TURN_ID, True)
    _ = resumed.begin_invocation(
        AGENT_KEY,
        TURN_ID,
        "reprime",
        ("new.py",),
        event_agent="caller",
        host="host",
        session_id="session",
    )

    physical = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    assert physical is not None
    assert all(entry.path != "new.py" for entry in physical.entries)
    current = resumed.current_snapshot
    assert current is not None
    deltas = calculate_net_delta(physical, current)
    assert [delta.path for delta in deltas] == ["new.py"]
    assert physical.snapshot_id == baseline.snapshot_id


def test_target_turn_is_not_garbage_collected_during_its_manifest_commit(
    tmp_path: Path,
) -> None:
    _lifecycle, baseline = _seed_active_turn(tmp_path)
    invocation = _invocation("old-but-active", ("app.py",))
    started = begin_invocation(tmp_path, invocation)
    assert started.incomplete is False
    ledger = load_ledger({"project_root": str(tmp_path)})
    active = ledger.get("active_turns")
    turn = active.get(AGENT_KEY) if isinstance(active, dict) else None
    assert isinstance(turn, dict)
    turn["last_event_at"] = "2000-01-01T00:00:00+00:00"
    assert save_ledger({"project_root": str(tmp_path)}, ledger)
    (tmp_path / "app.py").write_text("after", encoding="utf-8")

    report = observe_post_tool(tmp_path, invocation)

    committed = load_ledger({"project_root": str(tmp_path)})
    committed_turn = active_turn(committed, _prompt_payload(tmp_path, "unused"))
    physical = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    assert report.incomplete is False
    assert physical is not None and physical.snapshot_id == baseline.snapshot_id
    assert committed_turn is not None
    assert committed_turn["baseline_status"] == "ready"
    assert committed_turn["baseline_snapshot_id"] == physical.snapshot_id


def test_candidate_advance_cas_rejects_a_degraded_active_turn(tmp_path: Path) -> None:
    _lifecycle, baseline = _seed_active_turn(tmp_path)
    view = load_manifest_view(tmp_path)
    assert view.snapshot is not None
    (tmp_path / "late.py").write_text("candidate", encoding="utf-8")
    scanned = snapshot_workspace(tmp_path)
    merged = merge_turn_baseline(baseline, scanned, frozenset({"late.py"}))
    _ = record_event(
        _prompt_payload(tmp_path, baseline.snapshot_id)
        | {
            "event": "turn_bootstrap_degraded",
            "baseline_status": "degraded",
            "provenance_incomplete": True,
            "provenance_status": "incomplete",
            "provenance_status_reason": "baseline_state_mismatch",
        }
    )

    committed = commit_manifest(
        tmp_path,
        view.generation,
        view.snapshot.snapshot_id,
        view.snapshot,
        (),
        BaselineUpdate(
            AGENT_KEY,
            TURN_ID,
            baseline.snapshot_id,
            merged,
            frozenset({"late.py"}),
        ),
    )

    assert committed is None
    assert load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID) == baseline


def test_invocation_commit_rechecks_baseline_under_lock(tmp_path: Path) -> None:
    _lifecycle, baseline = _seed_active_turn(tmp_path)
    real_record = adapter_observation.record_turn_event_if_ready

    def degrade_before_record(
        root: Path,
        payload: dict[str, object],
        expected_baseline_snapshot_id: str,
    ) -> bool:
        _ = record_event(
            _prompt_payload(root, baseline.snapshot_id)
            | {
                "event": "turn_bootstrap_degraded",
                "baseline_status": "degraded",
                "provenance_incomplete": True,
                "provenance_status": "incomplete",
                "provenance_status_reason": "baseline_state_mismatch",
            }
        )
        return real_record(root, payload, expected_baseline_snapshot_id)  # type: ignore[arg-type]

    with patch.object(
        adapter_observation,
        "record_turn_event_if_ready",
        side_effect=degrade_before_record,
    ):
        report = begin_invocation(tmp_path, _invocation("raced"))

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    assert report.incomplete is True
    assert report.error_kind == "InvocationBaselineRace"
    assert turn is not None and turn["baseline_status"] == "degraded"
    invocations = turn.get("invocations", {})
    assert isinstance(invocations, dict) and "raced" not in invocations


def test_invocation_commit_retries_a_healthy_newer_baseline_winner(
    tmp_path: Path,
) -> None:
    _lifecycle, _baseline = _seed_active_turn(tmp_path)
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    real_record = adapter_observation.record_turn_event_if_ready
    calls = 0

    def advance_before_record(
        root: Path,
        payload: dict[str, object],
        expected_baseline_snapshot_id: str,
    ) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            (root / "b.py").write_text("b", encoding="utf-8")
            peer = ProvenanceLifecycle(root)
            peer.resume_turn(AGENT_KEY, TURN_ID, True)
            _ = peer.begin_invocation(AGENT_KEY, TURN_ID, "peer-b", ("b.py",))
        return real_record(root, payload, expected_baseline_snapshot_id)  # type: ignore[arg-type]

    with patch.object(
        adapter_observation,
        "record_turn_event_if_ready",
        side_effect=advance_before_record,
    ):
        report = begin_invocation(tmp_path, _invocation("winner-a", ("a.py",)))

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    physical = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    assert report.incomplete is False
    assert calls == 2
    assert physical is not None
    assert {entry.path for entry in physical.entries}.issuperset({"a.py", "b.py"})
    assert turn is not None and turn["baseline_status"] == "ready"
    assert turn["baseline_snapshot_id"] == physical.snapshot_id
    assert "winner-a" in turn["invocations"]


def test_old_turn_invocation_does_not_pollute_a_successor_turn(tmp_path: Path) -> None:
    _lifecycle, _baseline = _seed_active_turn(tmp_path)
    real_record = adapter_observation.record_turn_event_if_ready
    replaced = False

    def replace_turn_before_record(
        root: Path,
        payload: dict[str, object],
        expected_baseline_snapshot_id: str,
    ) -> object:
        nonlocal replaced
        if not replaced:
            replaced = True
            successor = ProvenanceLifecycle(root)
            started = successor.start_turn(AGENT_KEY, "new-turn", True)
            assert started.snapshot is not None
            new_baseline = load_turn_baseline(root, AGENT_KEY, "new-turn")
            assert new_baseline is not None
            _ = record_event(
                _prompt_payload(root, new_baseline.snapshot_id)
                | {"turn_id": "new-turn"}
            )
        return real_record(root, payload, expected_baseline_snapshot_id)  # type: ignore[arg-type]

    with patch.object(
        adapter_observation,
        "record_turn_event_if_ready",
        side_effect=replace_turn_before_record,
    ):
        report = begin_invocation(tmp_path, _invocation("stale-old"))

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(
        ledger,
        _prompt_payload(tmp_path, "unused") | {"turn_id": "new-turn"},
    )
    assert report.incomplete is True
    assert report.error_kind == "StaleTurn"
    assert turn is not None and turn["turn_id"] == "new-turn"
    assert turn["baseline_status"] == "ready"
    invocations = turn.get("invocations", {})
    assert isinstance(invocations, dict) and "stale-old" not in invocations


def test_trace_free_child_turn_is_remapped_to_the_live_parent(tmp_path: Path) -> None:
    _lifecycle, _baseline = _seed_active_turn(tmp_path)
    child = replace(
        _invocation("trace-free-child"),
        turn_id="previously-unseen-child-turn",
    )

    report = begin_invocation(tmp_path, child)

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    assert report.incomplete is False
    assert turn is not None and turn["turn_id"] == TURN_ID
    assert "trace-free-child" in turn["invocations"]


def test_turnless_guard_rejects_successor_audit_contamination(tmp_path: Path) -> None:
    _lifecycle, baseline = _seed_active_turn(tmp_path)
    successor_payload = _prompt_payload(tmp_path, baseline.snapshot_id) | {
        "turn_id": "new-turn"
    }
    _ = record_event(successor_payload)

    saved = record_event_if_current_turn(
        {
            "project_root": str(tmp_path),
            "event": "contract_authored",
            "host": "host",
            "session_id": "session",
            "agent": "caller",
            "turn_id": None,
            "contract_path": ".fable-lite/contracts/example.json",
            "content_digest": "digest",
        },
        allow_missing=True,
    )

    events = load_agent_events(str(tmp_path), "caller")
    assert saved is False
    assert events is not None
    assert all(event.get("event") != "contract_authored" for event in events)


def test_finished_turn_is_not_recreated_by_a_late_invocation(tmp_path: Path) -> None:
    _lifecycle, _baseline = _seed_active_turn(tmp_path)
    real_record = adapter_observation.record_turn_event_if_ready
    finished = False

    def finish_before_record(
        root: Path,
        payload: dict[str, object],
        expected_baseline_snapshot_id: str,
    ) -> object:
        nonlocal finished
        if not finished:
            finished = True
            _ = record_event(
                _prompt_payload(root, expected_baseline_snapshot_id)
                | {"event": "turn_finished"}
            )
        return real_record(root, payload, expected_baseline_snapshot_id)  # type: ignore[arg-type]

    with patch.object(
        adapter_observation,
        "record_turn_event_if_ready",
        side_effect=finish_before_record,
    ):
        report = begin_invocation(tmp_path, _invocation("late-after-finish"))

    ledger = load_ledger({"project_root": str(tmp_path)})
    assert report.incomplete is True
    assert report.error_kind == "StaleTurn"
    assert active_turn(ledger, _prompt_payload(tmp_path, "unused")) is None


def test_already_finished_turn_rejects_a_late_begin_before_entry(tmp_path: Path) -> None:
    _lifecycle, baseline = _seed_active_turn(tmp_path)
    _ = record_event(
        _prompt_payload(tmp_path, baseline.snapshot_id) | {"event": "turn_finished"}
    )

    report = begin_invocation(tmp_path, _invocation("late-entry"))

    ledger = load_ledger({"project_root": str(tmp_path)})
    assert report.incomplete is True
    assert report.error_kind == "StaleTurn"
    assert active_turn(ledger, _prompt_payload(tmp_path, "unused")) is None


def test_explicit_prompt_can_reopen_a_closed_turn_identity(tmp_path: Path) -> None:
    _lifecycle, baseline = _seed_active_turn(tmp_path)
    _ = record_event(
        _prompt_payload(tmp_path, baseline.snapshot_id) | {"event": "turn_finished"}
    )
    _ = record_event(_prompt_payload(tmp_path, baseline.snapshot_id, missing=True))

    report = begin_invocation(tmp_path, _invocation("reopened"))

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    assert report.incomplete is False
    assert turn is not None and turn["baseline_status"] == "ready"
    assert "reopened" in turn["invocations"]


def test_cross_session_explicit_old_turn_is_not_resolved_to_successor(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("current", encoding="utf-8")
    old_key = "codex_cli:old-session:codex"
    old = ProvenanceLifecycle(tmp_path)
    old_started = old.start_turn(old_key, "old-turn", True)
    assert old_started.snapshot is not None
    old_baseline = load_turn_baseline(tmp_path, old_key, "old-turn")
    assert old_baseline is not None
    old_payload = {
        "project_root": str(tmp_path),
        "host": "codex_cli",
        "session_id": "old-session",
        "agent": "codex",
        "turn_id": "old-turn",
        "baseline_snapshot_id": old_baseline.snapshot_id,
        "current_snapshot_id": old_baseline.snapshot_id,
        "provenance_incomplete": False,
        "provenance_status": "complete",
        "provenance_status_reason": "",
        "event": "prompt",
        "attribution": "exact",
    }
    _ = record_event(old_payload)
    _ = record_event(old_payload | {"event": "turn_finished"})
    successor_key = "codex_cli:new-session:codex"
    successor = ProvenanceLifecycle(tmp_path)
    started = successor.start_turn(successor_key, "new-turn", True)
    assert started.snapshot is not None
    successor_baseline = load_turn_baseline(tmp_path, successor_key, "new-turn")
    assert successor_baseline is not None
    successor_payload = {
        "project_root": str(tmp_path),
        "host": "codex_cli",
        "session_id": "new-session",
        "agent": "codex",
        "turn_id": "new-turn",
        "baseline_snapshot_id": successor_baseline.snapshot_id,
        "current_snapshot_id": successor_baseline.snapshot_id,
        "provenance_incomplete": False,
        "provenance_status": "complete",
        "provenance_status_reason": "",
        "event": "prompt",
        "attribution": "exact",
    }
    _ = record_event(successor_payload)
    stale = CanonicalInvocation(
        "codex_cli",
        "codex",
        "default",
        "old-turn",
        "stale-cross-session",
        "pre_tool",
        "edit",
        (),
        "",
        True,
        "",
        True,
        False,
    )

    report = begin_invocation(tmp_path, stale)

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, successor_payload)
    assert report.incomplete is True
    assert report.error_kind == "StaleTurn"
    assert turn is not None and turn["turn_id"] == "new-turn"
    assert not isinstance(turn.get("invocations"), dict) or (
        "stale-cross-session" not in turn["invocations"]
    )


def test_synthetic_identity_finds_closed_turn_in_another_session() -> None:
    ledger = default_v2_ledger()
    ledger["closed_turns"] = [
        {
            "agent_key": "codex_cli:old-session:codex",
            "turn_id": "old-turn",
            "closed_seq": 1,
        }
    ]
    invocation = CanonicalInvocation(
        "codex_cli",
        "codex",
        "default",
        "old-turn",
        "synthetic-old",
        "pre_tool",
        "edit",
        (),
        "",
        True,
        "",
        True,
        False,
    )

    assert adapter_observation._turn_closed_for_host_agent(ledger, invocation) is True
    assert (
        adapter_observation._turn_closed_for_host_agent(
            ledger,
            replace(invocation, host="other"),
        )
        is False
    )


def test_stale_stop_cannot_finish_or_restart_a_successor_turn(tmp_path: Path) -> None:
    _lifecycle, baseline = _seed_active_turn(tmp_path)
    successor_payload = _prompt_payload(tmp_path, baseline.snapshot_id) | {
        "turn_id": "new-turn"
    }
    _ = record_event(successor_payload)
    stale_stop = _prompt_payload(tmp_path, baseline.snapshot_id) | {
        "turn_id": TURN_ID,
        "stop_hook_active": False,
        "assistant_text": "done",
    }

    _ = evaluate_stop(stale_stop)

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, successor_payload)
    assert turn is not None and turn["turn_id"] == "new-turn"


def test_degraded_finalizer_does_not_recreate_a_just_finished_turn(
    tmp_path: Path,
) -> None:
    _lifecycle, _baseline = _seed_active_turn(tmp_path)
    real_record = adapter_observation.record_turn_event_if_ready
    finished = False

    def damage_then_finish(
        root: Path,
        payload: dict[str, object],
        expected_baseline_snapshot_id: str,
    ) -> object:
        nonlocal finished
        turn_baseline_path(root, AGENT_KEY, TURN_ID).write_text("{", encoding="utf-8")
        result = real_record(root, payload, expected_baseline_snapshot_id)  # type: ignore[arg-type]
        if not finished:
            finished = True
            _ = record_event(
                _prompt_payload(root, expected_baseline_snapshot_id)
                | {"event": "turn_finished"}
            )
        return result

    with patch.object(
        adapter_observation,
        "record_turn_event_if_ready",
        side_effect=damage_then_finish,
    ):
        report = begin_invocation(tmp_path, _invocation("damaged-after-finish"))

    ledger = load_ledger({"project_root": str(tmp_path)})
    assert report.incomplete is True
    assert report.error_kind == "StaleTurn"
    assert active_turn(ledger, _prompt_payload(tmp_path, "unused")) is None


def test_post_tool_commit_does_not_recreate_a_just_finished_turn(
    tmp_path: Path,
) -> None:
    _lifecycle, baseline = _seed_active_turn(tmp_path)
    invocation = _invocation("post-after-finish", ("app.py",))
    started = begin_invocation(tmp_path, invocation)
    assert started.incomplete is False
    (tmp_path / "app.py").write_text("after", encoding="utf-8")
    real_commit = provenance_lifecycle_module.commit_manifest
    finished = False

    def finish_before_commit(*args: object, **kwargs: object) -> object:
        nonlocal finished
        if not finished:
            finished = True
            _ = record_event(
                _prompt_payload(tmp_path, baseline.snapshot_id)
                | {"event": "turn_finished"}
            )
        return real_commit(*args, **kwargs)  # type: ignore[arg-type]

    with patch.object(
        provenance_lifecycle_module,
        "commit_manifest",
        side_effect=finish_before_commit,
    ):
        report = observe_post_tool(tmp_path, invocation)

    ledger = load_ledger({"project_root": str(tmp_path)})
    assert report.incomplete is True
    assert active_turn(ledger, _prompt_payload(tmp_path, "unused")) is None


def test_claimed_legacy_baseline_rejects_a_later_safe_key_collision(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("legacy", encoding="utf-8")
    first = CanonicalInvocation(
        "h",
        "a-b",
        "s",
        "turn:1",
        "first",
        "pre_tool",
        "edit",
        (),
        "",
        True,
        "",
    )
    first_lifecycle = ProvenanceLifecycle(tmp_path)
    started = first_lifecycle.start_turn(first.agent_key, first.turn_id, True)
    baseline = load_turn_baseline(tmp_path, first.agent_key, first.turn_id)
    assert started.snapshot is not None and baseline is not None
    path = turn_baseline_path(tmp_path, first.agent_key, first.turn_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["baseline_agent"]
    del raw["baseline_turn_id"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    first_identity = {
        "project_root": str(tmp_path),
        "host": first.host,
        "session_id": first.session_id,
        "agent": first.agent,
        "turn_id": first.turn_id,
        "invocation_id": first.invocation_id,
        "attribution": "exact",
    }
    _ = record_event(
        first_identity
        | {
            "event": "prompt",
            "baseline_snapshot_id": "snapshot:unavailable",
            "current_snapshot_id": "snapshot:unavailable",
            "provenance_incomplete": True,
            "provenance_status": "incomplete",
            "provenance_status_reason": "observation_error",
        }
    )
    adopted = begin_invocation(tmp_path, first)
    claimed = json.loads(path.read_text(encoding="utf-8"))
    assert adopted.incomplete is False
    assert claimed["baseline_agent"] == first.agent_key
    assert claimed["baseline_turn_id"] == first.turn_id
    _ = record_event(first_identity | {"event": "turn_finished"})

    second = CanonicalInvocation(
        "h-s",
        "b",
        "a",
        "turn-1",
        "second",
        "pre_tool",
        "edit",
        (),
        "",
        True,
        "",
    )
    assert second.agent_key != first.agent_key
    assert turn_baseline_path(tmp_path, second.agent_key, second.turn_id) == path

    rejected = begin_invocation(tmp_path, second)

    raw_after = json.loads(path.read_text(encoding="utf-8"))
    assert rejected.incomplete is True
    assert rejected.status_reason is ProvenanceReason.BASELINE_STATE_MISMATCH
    assert raw_after["baseline_agent"] == first.agent_key
    assert raw_after["baseline_turn_id"] == first.turn_id


def test_adapter_records_post_prime_physical_baseline_id(tmp_path: Path) -> None:
    _lifecycle, baseline = _baseline_only_candidate(tmp_path)

    report = begin_invocation(tmp_path, _invocation("adapter-late", ("late.py",)))

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    physical = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    assert report.incomplete is False
    assert physical is not None and physical.snapshot_id != baseline.snapshot_id
    assert turn is not None
    assert turn["baseline_status"] == "ready"
    assert turn["baseline_snapshot_id"] == physical.snapshot_id


def test_pending_candidate_recovers_after_workspace_before_baseline_cut(tmp_path: Path) -> None:
    lifecycle, baseline = _seed_active_turn(tmp_path)
    (tmp_path / "late.py").write_text("primed", encoding="utf-8")
    with patch.object(
        provenance_manifest,
        "advance_turn_baseline",
        side_effect=RuntimeError("crash after workspace current"),
    ):
        with pytest.raises(RuntimeError, match="crash after workspace current"):
            _ = lifecycle.begin_invocation(AGENT_KEY, TURN_ID, "late", ("late.py",))
    assert isinstance(load_ledger({"project_root": str(tmp_path)}).get("manifest_pending"), dict)
    assert load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID) == baseline

    _ = ProvenanceLifecycle(tmp_path)

    ledger = load_ledger({"project_root": str(tmp_path)})
    physical = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    assert "manifest_pending" not in ledger
    assert physical is not None and any(entry.path == "late.py" for entry in physical.entries)
    assert turn is not None and turn["baseline_snapshot_id"] == physical.snapshot_id


def test_corrupt_pending_baseline_clears_wal_and_degrades_turn(tmp_path: Path) -> None:
    lifecycle, _baseline = _seed_active_turn(tmp_path)
    (tmp_path / "late.py").write_text("primed", encoding="utf-8")
    with patch.object(
        provenance_manifest,
        "advance_turn_baseline",
        side_effect=RuntimeError("crash before baseline"),
    ):
        with pytest.raises(RuntimeError, match="crash before baseline"):
            _ = lifecycle.begin_invocation(AGENT_KEY, TURN_ID, "late", ("late.py",))
    turn_baseline_path(tmp_path, AGENT_KEY, TURN_ID).write_text("{", encoding="utf-8")

    _ = ProvenanceLifecycle(tmp_path)

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    assert "manifest_pending" not in ledger
    assert turn is not None and turn["baseline_status"] == "degraded"
    assert turn["provenance_status_reason"] == "baseline_state_mismatch"


def test_baseline_only_pending_recovers_when_manifest_ids_are_equal(tmp_path: Path) -> None:
    lifecycle, baseline = _baseline_only_candidate(tmp_path)
    generation = load_ledger({"project_root": str(tmp_path)})["manifest_generation"]
    with patch.object(
        provenance_manifest,
        "advance_turn_baseline",
        side_effect=RuntimeError("crash after baseline-only pending"),
    ):
        with pytest.raises(RuntimeError, match="baseline-only pending"):
            _ = lifecycle.begin_invocation(AGENT_KEY, TURN_ID, "late", ("late.py",))
    pending = load_ledger({"project_root": str(tmp_path)})["manifest_pending"]
    assert isinstance(pending, dict)
    assert pending["snapshot_before"] == pending["snapshot_after"]
    assert pending["target_generation"] == pending["base_generation"] == generation
    assert load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID) == baseline

    _ = ProvenanceLifecycle(tmp_path)

    ledger = load_ledger({"project_root": str(tmp_path)})
    physical = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    assert "manifest_pending" not in ledger
    assert ledger["manifest_generation"] == generation
    assert physical is not None and any(entry.path == "late.py" for entry in physical.entries)
    assert _entry_digest(physical, "app.py") == _entry_digest(baseline, "app.py")


def test_baseline_replace_before_final_ledger_save_recovers_exactly_once(
    tmp_path: Path,
) -> None:
    lifecycle, baseline = _baseline_only_candidate(tmp_path)
    real_save = provenance_manifest._save_or_raise
    saves = 0

    def crash_on_final_save(root: Path, payload: dict[str, object], ledger: dict[str, object]) -> None:
        nonlocal saves
        saves += 1
        if saves == 2:
            raise RuntimeError("crash before final ledger save")
        real_save(root, payload, ledger)  # type: ignore[arg-type]

    with patch.object(provenance_manifest, "_save_or_raise", side_effect=crash_on_final_save):
        with pytest.raises(RuntimeError, match="final ledger save"):
            _ = lifecycle.begin_invocation(AGENT_KEY, TURN_ID, "late", ("late.py",))
    physical_before_recovery = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)
    pending = load_ledger({"project_root": str(tmp_path)})["manifest_pending"]
    assert physical_before_recovery is not None
    assert physical_before_recovery.snapshot_id == pending["baseline_snapshot_after"]
    assert physical_before_recovery.snapshot_id != baseline.snapshot_id

    _ = ProvenanceLifecycle(tmp_path)
    first = load_ledger({"project_root": str(tmp_path)})
    _ = ProvenanceLifecycle(tmp_path)
    second = load_ledger({"project_root": str(tmp_path)})

    assert "manifest_pending" not in first
    assert first["manifest_generation"] == second["manifest_generation"]
    assert first["event_seq"] == second["event_seq"]


def test_pending_before_workspace_replace_rolls_back_without_advancing_baseline(
    tmp_path: Path,
) -> None:
    lifecycle, baseline = _seed_active_turn(tmp_path)
    (tmp_path / "late.py").write_text("not-committed", encoding="utf-8")
    with patch.object(
        provenance_manifest,
        "save_workspace_current",
        side_effect=RuntimeError("crash before workspace replace"),
    ):
        with pytest.raises(RuntimeError, match="workspace replace"):
            _ = lifecycle.begin_invocation(AGENT_KEY, TURN_ID, "late", ("late.py",))
    assert isinstance(load_ledger({"project_root": str(tmp_path)})["manifest_pending"], dict)

    recovered = ProvenanceLifecycle(tmp_path)
    ledger = load_ledger({"project_root": str(tmp_path)})
    physical = load_turn_baseline(tmp_path, AGENT_KEY, TURN_ID)

    assert "manifest_pending" not in ledger
    assert physical == baseline
    assert recovered.current_snapshot is not None
    assert all(entry.path != "late.py" for entry in recovered.current_snapshot.entries)


def test_degraded_turn_does_not_auto_repair_after_valid_baseline_returns(tmp_path: Path) -> None:
    _lifecycle, baseline = _seed_active_turn(tmp_path)
    turn_baseline_path(tmp_path, AGENT_KEY, TURN_ID).unlink()
    first = begin_invocation(tmp_path, _invocation("first"))
    save_turn_baseline(tmp_path, AGENT_KEY, TURN_ID, baseline)

    second = begin_invocation(tmp_path, _invocation("second"))
    _ = record_event(_prompt_payload(tmp_path, baseline.snapshot_id))

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    assert first.incomplete is True and second.incomplete is True
    assert turn is not None and turn["baseline_status"] == "degraded"
    assert turn["provenance_status_reason"] == "baseline_state_mismatch"


def test_post_tool_valid_baseline_mismatch_is_recorded_as_degraded(tmp_path: Path) -> None:
    _lifecycle, baseline = _seed_active_turn(tmp_path)
    invocation = _invocation("post-mismatch")
    started = begin_invocation(tmp_path, invocation)
    assert started.incomplete is False
    (tmp_path / "app.py").write_text("rogue baseline", encoding="utf-8")
    replacement = snapshot_workspace(tmp_path)
    assert replacement.snapshot_id != baseline.snapshot_id
    save_turn_baseline(tmp_path, AGENT_KEY, TURN_ID, replacement)

    report = observe_post_tool(tmp_path, invocation)

    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, _prompt_payload(tmp_path, "unused"))
    assert report.incomplete is True
    assert report.status_reason is ProvenanceReason.BASELINE_STATE_MISMATCH
    assert turn is not None and turn["baseline_status"] == "degraded"
    assert turn["baseline_snapshot_id"] == baseline.snapshot_id


def test_manifest_pending_schema_accepts_legacy_and_rejects_partial_baseline_cas() -> None:
    legacy = default_v2_ledger()
    legacy["manifest_pending"] = {
        "base_generation": 0,
        "target_generation": 1,
        "snapshot_before": "snapshot:unavailable",
        "snapshot_after": "snapshot:after",
        "events": [],
        "baseline_agent": AGENT_KEY,
        "baseline_turn_id": TURN_ID,
    }
    assert validate_v2_ledger(legacy) is legacy
    partial = deepcopy(legacy)
    pending = partial["manifest_pending"]
    assert isinstance(pending, dict)
    pending["baseline_snapshot_before"] = "snapshot:before"
    with pytest.raises(LedgerSchemaError, match="candidate baseline fields"):
        _ = validate_v2_ledger(partial)
    no_op = deepcopy(legacy)
    pending = no_op["manifest_pending"]
    assert isinstance(pending, dict)
    pending.update(
        {
            "target_generation": 0,
            "snapshot_before": "snapshot:same",
            "snapshot_after": "snapshot:same",
            "baseline_snapshot_before": "snapshot:baseline",
            "baseline_snapshot_after": "snapshot:baseline",
            "baseline_candidate_keys": [],
        }
    )
    with pytest.raises(LedgerSchemaError, match="baseline-only transitions"):
        _ = validate_v2_ledger(no_op)
    closed = default_v2_ledger()
    closed["closed_turns"] = [
        {"agent_key": AGENT_KEY, "turn_id": f"turn-{index}", "closed_seq": index}
        for index in range(257)
    ]
    with pytest.raises(LedgerSchemaError, match="at most 256"):
        _ = validate_v2_ledger(closed)
