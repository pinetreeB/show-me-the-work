from __future__ import annotations

from pathlib import Path

import core.ledger as ledger_module
from core.ledger import JsonObject, JsonValue, record_event
from core.verify_state import evaluate_stop, has_successful_verification
from fable_lite.check import evaluate as evaluate_check
from fable_lite.check_support import has_successful_verification as check_has_successful


def _payload(root: Path, event: str, agent: str = "alpha") -> dict[str, JsonValue]:
    return {
        "project_root": str(root),
        "event": event,
        "agent": agent,
        "host": "host",
        "session_id": "session",
    }


def _start(root: Path, agent: str = "alpha") -> JsonObject:
    return record_event(
        {
            **_payload(root, "prompt", agent),
            "task_mode": "deep",
            "prompt": f"{agent} change",
            "turn_id": f"{agent}-turn",
            "baseline_snapshot_id": "snapshot:base",
            "current_snapshot_id": "snapshot:base",
        }
    )


def _change(
    root: Path,
    event_id: str,
    change_id: str,
    path: str,
    before: str | None,
    after: str | None,
    kind: str = "code",
    agent: str = "alpha",
) -> JsonObject:
    return record_event(
        {
            **_payload(root, "change", agent),
            "event_id": event_id,
            "current_snapshot_id": f"snapshot:{event_id}",
            "paths": [
                {
                    "change_id": change_id,
                    "path": path,
                    "kind": kind,
                    "before": before,
                    "after": after,
                    "requires_verification": kind != "docs",
                }
            ],
        }
    )


def _covers(root: Path, invocation_id: str, agent: str = "alpha") -> JsonObject:
    return ledger_module.capture_verification_covers(
        {**_payload(root, "verification", agent), "invocation_id": invocation_id}
    )


def _verify(
    root: Path,
    covers: JsonObject,
    invocation_id: str,
    agent: str = "alpha",
) -> JsonObject:
    return record_event(
        {
            **_payload(root, "verification", agent),
            "invocation_id": invocation_id,
            "command": "python -m pytest tests/ -q",
            "success": True,
            "evidence": "1 passed",
            "covers": covers,
        }
    )


def _stop(root: Path, agent: str = "alpha") -> str:
    result = evaluate_stop(
        {**_payload(root, "stop", agent), "task_mode": "deep"}
    )
    decision = result["decision"]
    assert isinstance(decision, str)
    return decision


def test_verification_before_later_change_blocks_stop(tmp_path: Path) -> None:
    # Given: a verification covers the empty PreTool revision set.
    _ = _start(tmp_path)
    covers = _covers(tmp_path, "verify-before-edit")
    _ = _verify(tmp_path, covers, "verify-before-edit")

    # When: a shell-observed code revision is committed after verification.
    _ = _change(tmp_path, "change-1", "change:1", "app.py", "base", "after")

    # Then: the later revision is pending and Stop blocks.
    assert _stop(tmp_path) == "block"


def test_edit_then_verification_covers_matching_revision_for_stop_and_check(tmp_path: Path) -> None:
    # Given: a non-doc code revision exists before the verification process starts.
    _ = _start(tmp_path)
    _ = _change(tmp_path, "change-1", "change:1", "app.py", "base", "after")
    covers = _covers(tmp_path, "verify-after-edit")

    # When: successful verification records that frozen covers payload.
    assert covers["through_seq"] == 2
    assert covers["snapshot_id"] == "snapshot:change-1"
    assert covers["change_event_ids"] == ["change-1"]
    revisions = covers["path_revisions"]
    assert revisions == [
        {
            "change_id": "change:1",
            "path": "app.py",
            "after": "after",
            "change_event_id": "change-1",
        }
    ]
    verified = _verify(tmp_path, covers, "verify-after-edit")

    # Then: Stop and check-support share the covers-based allow decision.
    assert has_successful_verification(verified) is True
    assert check_has_successful(verified) is True
    assert evaluate_check(tmp_path, "alpha", None).unverified == []
    assert _stop(tmp_path) == "allow"


def test_same_invocation_modify_and_verify_does_not_cover_posttool_change(tmp_path: Path) -> None:
    # Given: PreTool captured an empty covers set for one verification invocation.
    _ = _start(tmp_path)
    covers = _covers(tmp_path, "combined")

    # When: the invocation mutates a code path before its verification event commits.
    _ = _change(tmp_path, "change-1", "change:1", "app.py", "base", "after")
    verified = _verify(tmp_path, covers, "combined")

    # Then: frozen covers excludes that change and Stop remains blocked.
    results = verified["verification_results"]
    assert isinstance(results, list)
    result = results[-1]
    assert isinstance(result, dict)
    recorded = result["covers"]
    assert isinstance(recorded, dict)
    assert recorded["change_ids"] == []
    assert _stop(tmp_path) == "block"


def test_docs_revision_after_covered_code_stays_exempt(tmp_path: Path) -> None:
    # Given: a code revision is already covered by successful verification.
    _ = _start(tmp_path)
    _ = _change(tmp_path, "change-1", "change:1", "app.py", "base", "after")
    _ = _verify(
        tmp_path, _covers(tmp_path, "covered-code"), "covered-code"
    )

    # When: a later docs-only revision is committed.
    _ = _change(tmp_path, "change-2", "change:2", "README.md", "old", "new", "docs")

    # Then: the docs audit remains while verification pending stays clear.
    assert _stop(tmp_path) == "allow"


def test_generated_non_doc_revision_is_pending(tmp_path: Path) -> None:
    # Given: a prior code revision is covered successfully.
    _ = _start(tmp_path)
    _ = _change(tmp_path, "change-1", "change:1", "app.py", "base", "after")
    _ = _verify(
        tmp_path, _covers(tmp_path, "covered-code"), "covered-code"
    )

    # When: a generated but non-doc revision appears.
    _ = _change(tmp_path, "change-2", "change:2", "dist/app.js", "old", "new", "artifact")

    # Then: generated source does not bypass the pending requirement.
    assert _stop(tmp_path) == "block"


def test_baseline_revert_removes_pending_without_deleting_audit(tmp_path: Path) -> None:
    # Given: a code path has departed from its baseline revision.
    _ = _start(tmp_path)
    _ = _change(tmp_path, "change-1", "change:1", "app.py", "base", "after")

    # When: the same path returns to the original baseline digest.
    reverted = _change(tmp_path, "change-2", "change:2", "app.py", "after", "base")

    # Then: audit remains, but the net pending set allows Stop without verification.
    active = reverted["active_turns"]
    assert isinstance(active, dict)
    turn = active["host:session:alpha"]
    assert isinstance(turn, dict)
    assert turn["pending_change_ids"] == []
    assert _stop(tmp_path) == "allow"


def test_verification_after_same_path_rechange_becomes_stale(tmp_path: Path) -> None:
    # Given: a first code revision has a matching successful covers record.
    _ = _start(tmp_path)
    _ = _change(tmp_path, "change-1", "change:1", "app.py", "base", "after-one")
    _ = _verify(
        tmp_path, _covers(tmp_path, "first-revision"), "first-revision"
    )

    # When: the same path receives a newer net revision.
    changed = _change(tmp_path, "change-2", "change:2", "app.py", "after-one", "after-two")

    # Then: the old covers is stale for both Stop and check-support.
    assert has_successful_verification(changed) is False
    assert check_has_successful(changed) is False
    assert evaluate_check(tmp_path, "alpha", None).unverified == ["app.py"]
    assert _stop(tmp_path) == "block"


def test_agent_b_verification_does_not_cover_agent_a_posttool_revision(tmp_path: Path) -> None:
    # Given: B fixed its verification covers before A's later mutation.
    _ = _start(tmp_path, "alpha")
    _ = _start(tmp_path, "beta")
    covers = _covers(tmp_path, "beta-verify", "beta")

    # When: A commits a code revision and B records its earlier verification.
    _ = _change(tmp_path, "change-a", "change:a", "app.py", "base", "after", agent="alpha")
    _ = _verify(tmp_path, covers, "beta-verify", "beta")

    # Then: A's post-PreTool revision remains unverified.
    assert _stop(tmp_path, "alpha") == "block"
