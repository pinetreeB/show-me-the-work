"""v2.6.2 ATTR-03 — symlink write-through attribution (RED-first).

PostTool candidate match는 logical key OR invocation-time resolved key로 판정한다.
- symlink replacement → logical match
- write-through → resolved match
- retarget race/out-of-root → invocation 시점 키에 없으므로 external(fail-closed)

RED 핵심: link->real 심릭에 Edit(link)가 real로 write-through하면 snapshot
delta는 real.py로 잡히는데, match set이 logical {link.py}만 보면 external로
오귀속된다.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from core.adapter_observation import (
    CanonicalInvocation,
    _stored_candidates,
    begin_invocation,
)
from core.ledger import record_event
from core.provenance_lifecycle import ProvenanceLifecycle
from core.provenance_store import load_turn_baseline
from core.provenance_types import ProvenanceStatus


HOST = "codex_cli"
AGENT = "codex"
SESSION_ID = "attr-session"
TURN_ID = "turn:attr-session:codex"
AGENT_KEY = f"{HOST}:{SESSION_ID}:{AGENT}"


def _invocation(
    invocation_id: str, phase: str, family: str, candidates: tuple[str, ...]
) -> CanonicalInvocation:
    return CanonicalInvocation(
        HOST,
        AGENT,
        SESSION_ID,
        TURN_ID,
        invocation_id,
        phase,
        family,
        candidates,
        "",
        True,
        "",
    )


def _start(root: Path) -> None:
    # test_f2_atomicity의 검증된 시드 패턴: lifecycle start_turn + baseline id가
    # 포함된 prompt 이벤트(어댑터 수준 start_turn 아님).
    lifecycle = ProvenanceLifecycle(root)
    result = lifecycle.start_turn(AGENT_KEY, TURN_ID, True)
    baseline = load_turn_baseline(root, AGENT_KEY, TURN_ID)
    assert result.snapshot is not None and baseline is not None
    _ = record_event(
        {
            "project_root": str(root),
            "event": "prompt",
            "host": HOST,
            "agent": AGENT,
            "session_id": SESSION_ID,
            "turn_id": TURN_ID,
            "attribution": "exact",
            "prompt": "edit through the link",
            "baseline_snapshot_id": baseline.snapshot_id,
            "current_snapshot_id": baseline.snapshot_id,
            "provenance_incomplete": False,
            "provenance_status": "complete",
            "provenance_status_reason": "",
        }
    )


def _begin_edit(root: Path, candidates: tuple[str, ...]) -> None:
    # 어댑터 수준 begin_invocation — ledger에 logical+resolved candidate 기록.
    _ = begin_invocation(root, _invocation("edit-1", "pre_tool", "edit", candidates))


def _observe_sources(root: Path, candidates: tuple[str, ...]) -> dict[str, str]:
    """관측 체인: _stored_candidates(수정 대상) → lifecycle post_tool → source.

    기존 multiagent 테스트의 확립된 surface(result.changes[].source)를 쓰되
    candidate는 어댑터가 ledger에서 재구성한 그대로 넘긴다.
    """
    invocation = _invocation("edit-1", "post_tool", "edit", candidates)
    stored = _stored_candidates(root, invocation)
    lifecycle = ProvenanceLifecycle(root)
    lifecycle.resume_turn(AGENT_KEY, TURN_ID, True, require_ledger_turn=True)
    started = lifecycle.begin_invocation(
        AGENT_KEY,
        TURN_ID,
        "edit-1-post",
        stored,
        prime_candidates=False,
    )
    result = lifecycle.post_tool(started, "edit")
    return {change.path: change.source for change in result.changes}


def _symlink(target: str, link: Path) -> None:
    try:
        os.symlink(target, link)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {exc}")


# ---------------------------------------------------------------------------
# RED 핵심: write-through는 resolved match로 소유 귀속
# ---------------------------------------------------------------------------


def test_attr_03_symlink_write_through_is_owned_not_external(tmp_path: Path) -> None:
    real = tmp_path / "real.py"
    real.write_text("v1\n", encoding="utf-8")
    _symlink("real.py", tmp_path / "link.py")
    _start(tmp_path)
    _begin_edit(tmp_path, ("link.py",))

    # Edit(link.py)의 물리 효과: 링크 타깃(real.py) 내용 변경.
    real.write_text("v2 edited through link\n", encoding="utf-8")
    sources = _observe_sources(tmp_path, ("link.py",))

    # 수정 전: match set이 logical {link.py}만 봐서 real.py가 external(RED).
    assert sources.get("real.py") == "edit"


def test_attr_03_broken_symlink_write_through_is_owned_via_resolved_target(
    tmp_path: Path,
) -> None:
    _symlink("missing.py", tmp_path / "link.py")  # dangling at invocation time
    _start(tmp_path)
    _begin_edit(tmp_path, ("link.py",))

    # write-through가 dangling 타깃을 생성한다.
    (tmp_path / "missing.py").write_text("created through broken link\n", encoding="utf-8")
    sources = _observe_sources(tmp_path, ("link.py",))

    assert sources.get("missing.py") == "edit"


def test_attr_03_stored_candidates_union_logical_and_resolved(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real.py"
    real.write_text("v1\n", encoding="utf-8")
    _symlink("real.py", tmp_path / "link.py")
    _start(tmp_path)
    _begin_edit(tmp_path, ("link.py",))

    stored = _stored_candidates(
        tmp_path, _invocation("edit-1", "post_tool", "edit", ("link.py",))
    )

    # 수정 대상: logical(link.py) + invocation-time resolved(real.py) 합집합.
    assert set(stored) >= {"link.py", "real.py"}


# ---------------------------------------------------------------------------
# 회귀 가드: logical match·fail-closed·경계 사례
# ---------------------------------------------------------------------------


def test_attr_03_symlink_replacement_matches_logical_key(tmp_path: Path) -> None:
    real = tmp_path / "real.py"
    real.write_text("v1\n", encoding="utf-8")
    link = tmp_path / "link.py"
    _symlink("real.py", link)
    _start(tmp_path)
    _begin_edit(tmp_path, ("link.py",))

    # 링크 자체를 일반 파일로 교체 → logical key delta.
    os.remove(link)
    link.write_text("now a regular file\n", encoding="utf-8")
    sources = _observe_sources(tmp_path, ("link.py",))

    assert sources.get("link.py") == "edit"


def test_attr_03_retarget_race_stays_external_fail_closed(tmp_path: Path) -> None:
    real_a = tmp_path / "real_a.py"
    real_b = tmp_path / "real_b.py"
    real_a.write_text("a v1\n", encoding="utf-8")
    real_b.write_text("b v1\n", encoding="utf-8")
    link = tmp_path / "link.py"
    _symlink("real_a.py", link)
    _start(tmp_path)
    _begin_edit(tmp_path, ("link.py",))  # invocation-time resolved = real_a.py

    # invocation 후 링크가 real_b로 재지정되고 real_b가 변경된다.
    os.remove(link)
    _symlink("real_b.py", link)
    real_b.write_text("b v2\n", encoding="utf-8")
    sources = _observe_sources(tmp_path, ("link.py",))

    # invocation 시점 키{link, real_a}에 없으므로 external(fail-closed가 정확).
    assert sources.get("real_b.py") == "external"


def test_attr_03_out_of_root_target_makes_no_owned_claim(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.py"
    secret.write_text("v1\n", encoding="utf-8")
    root = tmp_path / "proj"
    root.mkdir()
    _symlink(os.path.join("..", "outside", "secret.py"), root / "link.py")
    _start(root)
    _begin_edit(root, ("link.py",))

    secret.write_text("v2\n", encoding="utf-8")
    sources = _observe_sources(root, ("link.py",))

    assert not any("secret.py" in path for path in sources)


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")
def test_attr_03_windows_junction_degrades_to_incomplete_fail_closed(
    tmp_path: Path,
) -> None:
    # junction(reparse point)은 현재 스캐너가 관측하지 못한다 — ATTR-03 범위 밖의
    # 스캐너 한계. 안전 방향: INCOMPLETE로 degrade돼 clean을 주장하지 못한다
    # (Stop 게이트가 미검증 완료를 막는다).
    realdir = tmp_path / "realdir"
    realdir.mkdir()
    (realdir / "data.txt").write_text("v1\n", encoding="utf-8")
    linkdir = tmp_path / "linkdir"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(linkdir), str(realdir)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not linkdir.exists():
        pytest.skip(f"junction unavailable: {result.stderr.strip()}")

    lifecycle = ProvenanceLifecycle(tmp_path)
    started = lifecycle.start_turn(AGENT_KEY, TURN_ID, True)

    assert started.status is ProvenanceStatus.INCOMPLETE


@pytest.mark.skipif(os.name != "nt", reason="casefold matching is Windows-only")
def test_attr_03_windows_casefold_candidate_matches_manifest(tmp_path: Path) -> None:
    (tmp_path / "Data.txt").write_text("v1\n", encoding="utf-8")
    _start(tmp_path)
    _begin_edit(tmp_path, ("data.txt",))

    (tmp_path / "Data.txt").write_text("v2\n", encoding="utf-8")
    sources = _observe_sources(tmp_path, ("data.txt",))

    assert sources.get("Data.txt") == "edit"
