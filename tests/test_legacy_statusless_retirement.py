"""W5 — legacy status-less turn 퇴역 경로 (RED-first).

배경(2026-08-07 실측): 구 스키마 invocation은 ``status`` 필드가 없다. 그 row는
``started_at``도 없어 lease 판정이 불가능하므로 자동 close가 구조적으로 금지된다
(`core/ledger_migration.py`). 그런데 maintenance는 **턴 단위**로 정리하고 형식 검사는
**남은 장부 전체**를 보기 때문에, 후보에서 제외된 턴(current identity·unsettled
attribution)에 status-less가 남으면 apply 전체가 롤백된다 → 마이그레이션 영구 고착.

계약:
- status-less 보유 턴은 ``closed``(완료)로 절대 기록하지 않는다. 증거 없는 완료 표시는
  이 하네스가 막으려는 바로 그 행위다.
- 퇴역은 운영자가 영향 invocation 수를 정확히 대야만(``ack_unverified``) 열린다.
- 원문 bytes를 archive에 보관하고 ``retired_turns``가 그 digest를 참조한다.
- ``path_attribution`` owner는 건드리지 않는다(경로 보호 유지).
- 퇴역 턴은 closed와 함께 terminal로 취급되어 같은 turn id의 replay를 막는다.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

from core.ledger_maintenance import apply_maintenance_plan, plan_maintenance
from core.ledger_storage import ledger_path
from core.ledger_v2 import (
    apply_v2_event,
    default_v2_ledger,
    turn_is_closed,
    turn_is_retired,
    turn_is_terminal,
)

STALE = datetime.now(UTC) - timedelta(days=3)


def _write(root: Path, ledger: dict) -> Path:
    path = ledger_path(str(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def _read(root: Path) -> dict:
    return json.loads(ledger_path(str(root)).read_text(encoding="utf-8"))


def _seed_turn(ledger: dict, agent: str, *, seq: int, timestamp: datetime | None = None) -> str:
    event: dict = {
        "event": "prompt",
        "seq": seq,
        "host": "host",
        "session_id": f"session-{agent}",
        "agent": agent,
        "turn_id": f"turn-{agent}",
        "prompt": f"{agent} work",
        "baseline_snapshot_id": "snapshot:base",
        "current_snapshot_id": "snapshot:base",
    }
    if timestamp is not None:
        event["timestamp"] = timestamp.isoformat()
    _ = apply_v2_event(ledger, event)
    return f"host:session-{agent}:{agent}"


def _make_statusless(ledger: dict, agent_key: str, count: int) -> None:
    """구 스키마 잔재 재현: status·started_at 없이 candidate_paths만 가진 invocation."""
    turn = ledger["active_turns"][agent_key]
    invocations = turn.get("invocations")
    invocations = invocations if isinstance(invocations, dict) else {}
    for index in range(count):
        invocations[f"legacy-{agent_key}-{index}"] = {"candidate_paths": [f"src/legacy{index}.py"]}
    turn["invocations"] = invocations


def _unsettled_owner(ledger: dict, agent_key: str, path: str = "src/shared.py") -> None:
    attribution = ledger.get("path_attribution")
    attribution = attribution if isinstance(attribution, dict) else {}
    attribution[path] = {
        "generation": 1,
        "status": "exclusive",
        "owners": [
            {
                "agent_key": agent_key,
                "turn_id": f"turn-{agent_key.split(':')[-1]}",
                "revision_seq": 1,
                "after_digest": "digest:x",
                "invocation_id": "inv-unsettled",
                "settled": False,
            }
        ],
    }
    ledger["path_attribution"] = attribution


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """current turn·unsettled turn·정상 후보를 한 장부에 담는다.

    현행 롤백을 재현하려면 이 셋이 동시에 있어야 한다. 기존 테스트는 제외 사유를
    각각 따로만 검증해서 이 조합을 만들지 못했다.
    """
    ledger = default_v2_ledger()
    plain = _seed_turn(ledger, "plain", seq=10, timestamp=STALE)
    unsettled = _seed_turn(ledger, "unsettled", seq=20, timestamp=STALE)
    current = _seed_turn(ledger, "claude", seq=30, timestamp=STALE)
    ledger["agent"] = "claude"  # payload 없는 active_turn()이 이 턴을 current로 고른다
    _make_statusless(ledger, unsettled, 4)
    _make_statusless(ledger, current, 2)
    _unsettled_owner(ledger, unsettled)
    _write(tmp_path, ledger)
    return tmp_path, {"plain": plain, "unsettled": unsettled, "current": current}


def test_plan_reports_statusless_turns_with_exclusion_reasons(tmp_path: Path) -> None:
    root, keys = _fixture(tmp_path)
    plan = plan_maintenance(str(root))

    statusless = {c.agent_key: c for c in plan.legacy_statusless}
    assert set(statusless) == {keys["unsettled"], keys["current"]}
    assert plan.statusless_invocations == 6
    assert "unsettled_attribution" in statusless[keys["unsettled"]].exclusions
    assert "current_turn" in statusless[keys["current"]].exclusions
    # 정상 후보는 별도 카테고리로 남는다
    assert keys["plain"] in {c.agent_key for c in plan.safe_auto}


def test_apply_without_retire_flag_still_rolls_back(tmp_path: Path) -> None:
    """RED 재현: 퇴역 경로 없이는 status-less가 남아 형식 검사가 전체를 되돌린다."""
    root, _ = _fixture(tmp_path)
    before = ledger_path(str(root)).read_bytes()
    plan = plan_maintenance(str(root))

    result = apply_maintenance_plan(str(root), plan, include_manual_only=True)

    assert result.rejected is True
    assert result.reason.startswith("validate:")
    assert result.removed == 0
    assert ledger_path(str(root)).read_bytes() == before


def test_retire_requires_explicit_acknowledgement(tmp_path: Path) -> None:
    root, _ = _fixture(tmp_path)
    before = ledger_path(str(root)).read_bytes()
    plan = plan_maintenance(str(root))

    missing = apply_maintenance_plan(str(root), plan, retire_legacy_statusless=True)
    assert missing.rejected is True
    assert missing.reason == "ack_unverified_required"

    wrong = apply_maintenance_plan(
        str(root), plan, retire_legacy_statusless=True, ack_unverified=999
    )
    assert wrong.rejected is True
    assert wrong.reason.startswith("ack_mismatch:")
    assert ledger_path(str(root)).read_bytes() == before


def test_retire_moves_statusless_turns_without_marking_them_completed(tmp_path: Path) -> None:
    root, keys = _fixture(tmp_path)
    original = ledger_path(str(root)).read_bytes()
    plan = plan_maintenance(str(root))

    result = apply_maintenance_plan(
        str(root),
        plan,
        include_manual_only=True,
        retire_legacy_statusless=True,
        ack_unverified=6,
    )

    assert result.rejected is False
    assert result.retired == 2
    after = _read(root)

    for key in (keys["unsettled"], keys["current"]):
        turn_id = f"turn-{key.split(':')[-1]}"
        assert turn_is_retired(after, key, turn_id) is True
        assert turn_is_closed(after, key, turn_id) is False, "증거 없는 턴을 완료로 기록했다"
        assert turn_is_terminal(after, key, turn_id) is True

    # 정상 후보는 여전히 완료로 간다
    assert turn_is_closed(after, keys["plain"], "turn-plain") is True

    # 퇴역 기록은 사유와 archive digest를 갖는다
    for entry in after["retired_turns"]:
        assert entry["resolution"] == "unverified_legacy"
        assert entry["archive_digest"] == sha256(original).hexdigest()

    # 남은 status-less 0건
    remaining = [
        invocation
        for turn in after["active_turns"].values()
        for invocation in (turn.get("invocations") or {}).values()
        if isinstance(invocation, dict) and "status" not in invocation
    ]
    assert remaining == []


def test_retire_archives_original_bytes_verbatim(tmp_path: Path) -> None:
    root, _ = _fixture(tmp_path)
    original = ledger_path(str(root)).read_bytes()
    plan = plan_maintenance(str(root))

    result = apply_maintenance_plan(
        str(root), plan, retire_legacy_statusless=True, ack_unverified=6
    )

    assert result.archive_path is not None
    archive = Path(result.archive_path)
    assert archive.read_bytes() == original
    assert sha256(archive.read_bytes()).hexdigest() == plan.source_digest


def test_retire_preserves_unsettled_path_attribution(tmp_path: Path) -> None:
    root, _ = _fixture(tmp_path)
    before = _read(root)["path_attribution"]
    plan = plan_maintenance(str(root))

    _ = apply_maintenance_plan(
        str(root), plan, retire_legacy_statusless=True, ack_unverified=6
    )

    assert _read(root)["path_attribution"] == before, "미정산 경로 소유권이 변경됐다"


def test_retired_turn_blocks_same_turn_replay(tmp_path: Path) -> None:
    """퇴역 턴을 terminal로 보지 않으면 같은 turn id 이벤트가 되살아난다."""
    root, keys = _fixture(tmp_path)
    plan = plan_maintenance(str(root))
    _ = apply_maintenance_plan(
        str(root), plan, retire_legacy_statusless=True, ack_unverified=6
    )
    after = _read(root)
    key = keys["unsettled"]
    assert key not in after["active_turns"]

    replayed = apply_v2_event(
        after,
        {
            "event": "invocation",
            "seq": 900,
            "host": "host",
            "session_id": "session-unsettled",
            "agent": "unsettled",
            "turn_id": "turn-unsettled",
            "invocation_id": "replay-1",
            "candidate_paths": ["src/legacy0.py"],
        },
    )

    assert key not in replayed.get("active_turns", {}), "퇴역 턴이 replay로 되살아났다"


def test_prompt_event_cannot_revive_retired_turn(tmp_path: Path) -> None:
    """PR #56 리뷰 지적 P1: prompt는 terminal 검사를 우회한다.

    `event != "prompt"` 가드 때문에 UserPromptSubmit이 퇴역한 turn id로 재생되면
    prompt 분기가 그 턴을 active_turns에 다시 만든다. retired_turns 항목은 그대로
    남으므로 **같은 턴이 퇴역과 활성 양쪽에 존재**하게 된다. Codex·Claude prompt
    어댑터는 record_event를 직접 부르므로 상위 가드도 거치지 않는다.
    """
    root, keys = _fixture(tmp_path)
    plan = plan_maintenance(str(root))
    _ = apply_maintenance_plan(
        str(root), plan, retire_legacy_statusless=True, ack_unverified=6
    )
    after = _read(root)
    key = keys["unsettled"]
    assert key not in after["active_turns"]

    revived = apply_v2_event(
        after,
        {
            "event": "prompt",
            "seq": 950,
            "host": "host",
            "session_id": "session-unsettled",
            "agent": "unsettled",
            "turn_id": "turn-unsettled",
            "prompt": "replayed prompt",
            "baseline_snapshot_id": "snapshot:base",
            "current_snapshot_id": "snapshot:base",
        },
    )

    assert key not in revived.get("active_turns", {}), (
        "퇴역 턴이 prompt 재생으로 되살아났다 — 운영자가 퇴역시킨 판단이 무효화된다"
    )
    assert turn_is_retired(revived, key, "turn-unsettled") is True


def test_retired_turn_found_across_synthetic_session_keys(tmp_path: Path) -> None:
    """PR #56 리뷰 지적 P2: host/agent 교차 조회가 closed_turns만 본다.

    퇴역 턴이 옛 세션 키로 기록돼 있으면, 새 세션 키로 turn_is_terminal을 물어봐도
    찾지 못한다. closed 턴에는 이를 메우는 fallback이 있으나 retired에는 없어서
    stale invocation이 조용히 새 턴에 바인딩된다.
    """
    from core.adapter_observation import CanonicalInvocation, _turn_terminal_for_host_agent

    ledger = default_v2_ledger()
    ledger["retired_turns"] = [
        {
            "agent_key": "host:old-session:agentx",
            "turn_id": "turn-legacy",
            "retired_seq": 5,
            "resolution": "unverified_legacy",
            "archive_digest": "digest:x",
        }
    ]

    invocation = CanonicalInvocation(
        host="host",
        agent="agentx",
        session_id="new-session",
        turn_id="turn-legacy",
        invocation_id="inv-1",
        phase="pre",
        tool_family_hint="edit",
        candidate_paths=(),
        command_hint="",
        success=True,
        evidence="",
        identity_synthetic=True,
    )

    assert _turn_terminal_for_host_agent(ledger, invocation) is True, (
        "옛 세션 키로 퇴역된 턴을 새 세션 키에서 찾지 못한다"
    )


def test_repeated_retire_is_idempotent(tmp_path: Path) -> None:
    root, _ = _fixture(tmp_path)
    plan = plan_maintenance(str(root))
    first = apply_maintenance_plan(
        str(root), plan, retire_legacy_statusless=True, ack_unverified=6
    )
    assert first.rejected is False

    # 두 번째 실행: 남은 status-less가 없으므로 퇴역 대상도 없다
    second_plan = plan_maintenance(str(root))
    assert second_plan.legacy_statusless == ()
    second = apply_maintenance_plan(
        str(root), second_plan, retire_legacy_statusless=True, ack_unverified=0
    )
    assert second.rejected is False
    assert second.retired == 0
    archives = list(ledger_path(str(root)).parent.glob("ledger.legacy-statusless-*.bak"))
    assert len(archives) == 1, "재실행이 중복 archive를 만들었다"


def test_stale_plan_digest_is_rejected(tmp_path: Path) -> None:
    root, keys = _fixture(tmp_path)
    plan = plan_maintenance(str(root))

    mutated = _read(root)
    mutated["event_seq"] = 4242
    _write(root, mutated)
    after_mutation = ledger_path(str(root)).read_bytes()

    result = apply_maintenance_plan(
        str(root), plan, retire_legacy_statusless=True, ack_unverified=6
    )

    assert result.rejected is True
    assert result.reason == "digest_mismatch"
    assert ledger_path(str(root)).read_bytes() == after_mutation
    assert keys["current"] in _read(root)["active_turns"]
