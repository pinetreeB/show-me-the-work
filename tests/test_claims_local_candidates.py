from pathlib import Path
import random

from smtw.claims.assist import select_candidates
from smtw.claims.packets import PacketSource, build_packets


SHA = "a" * 40


def claim(text, reason, *, line=1, source=None):
    return {"report_line": line, "check_type": "K4", "text": text,
            "verdict": "unverifiable", "reason_code": reason, "source": source or {}}


def test_local_candidate_expansion_is_opt_in_and_preserves_same_line_atoms():
    report = "첫 문장이다. 둘째 문장이다."
    checked = {"claims": [
        claim("첫 문장이다.", "binding_ambiguous"),
        claim("둘째 문장이다.", "binding_ambiguous"),
    ]}
    default = select_candidates(report, checked)
    local = select_candidates(report, checked, local_candidates=True)
    assert default["selected_count"] == 0
    assert default["excluded_reason_counts"] == {"binding_ambiguous": 2}
    assert [item["branch"] for item in local["items"]] == ["binding_ambiguous", "binding_ambiguous"]
    assert len({item["id"] for item in local["items"]}) == 2
    assert {item["claim"] for item in local["items"]} == {"첫 문장이다.", "둘째 문장이다."}


def test_local_other_file_candidate_requires_fixed_hit_and_transmits_covered_target():
    report = "`logic.py:1` 함수 `moved_name` 정의가 있다."
    hit = {"path": "other.ts", "line": 2, "revision": SHA, "kind": "definition"}
    checked = {"claims": [claim("logic.py:1", "found_outside_scope", source={"repository_hits": [hit]})]}
    checked["claims"][0]["check_type"] = "K1"
    assert select_candidates(report, checked, local_candidates=True)["selected_count"] == 0
    selected = select_candidates(report, checked, fixed_commit=True, local_candidates=True)
    item, = selected["items"]
    assert item["paths"] == ["logic.py", "other.ts"]
    assert item["ranges"] == [{"path": "logic.py", "start": 1, "end": 1},
                               {"path": "logic.py", "start": 1, "end": 1},
                               {"path": "other.ts", "start": 2, "end": 2}]
    packets, summary = build_packets(
        selected,
        [PacketSource("C:/synthetic/repo/logic.py", {1: "export const original = 1;"},
                      "fixed_commit", revision=SHA),
         PacketSource("C:/synthetic/repo/other.ts", {1: "header", 2: "export const moved_name = 1;"},
                      "fixed_commit", revision=SHA)],
        report=Path("report.md"), rng=random.Random(0), local_candidates=True,
    )
    assert summary["sent_count"] == 1
    sent, = packets[0]["data_block"]
    assert sent["fixed_commit"] is True
    assert sent["atom"] == {"check_type": "K1", "sentence_start": 0,
                            "sentence_end": len(report), "ordinal": 0}
    assert sent["target_evidence"] == [{"path": "C:/synthetic/repo/logic.py", "start_line": 1,
                                         "end_line": 1, "excerpt_index": 1}]
    assert sent["other_file_evidence"] == [{"path": "C:/synthetic/repo/other.ts", "start_line": 2,
                                             "end_line": 2, "revision": SHA, "excerpt_index": 2}]


def test_local_other_file_candidate_without_fixed_line_proof_is_excluded():
    report = "`logic.py:1` 함수 `moved_name` 정의가 있다."
    checked = {"claims": [claim("logic.py:1", "found_outside_scope", source={
        "repository_hits": [{"path": "other.ts", "line": 2, "revision": "short"}],
    })]}
    selected = select_candidates(report, checked, fixed_commit=True, local_candidates=True)
    assert selected["selected_count"] == 0
    assert selected["excluded_reason_counts"] == {"fixed_target_missing": 1}


def test_local_packet_keeps_each_required_target_when_one_is_not_transmitted():
    selected = {"selected_count": 1, "deferred_count": 0, "items": [{
        "id": "two-targets", "branch": "partial_range", "report_line": 1,
        "claim": "synthetic claim", "before": "", "after": "", "paths": ["logic.py"],
        "identifiers": [], "ranges": [{"path": "logic.py", "start": 1, "end": 1}],
        "target_evidence": [{"path": "logic.py", "start": 1, "end": 1},
                            {"path": "logic.py", "start": 3, "end": 3}],
        "atom": {"check_type": "K1", "reason_code": "partial_range",
                 "sentence_start": 0, "sentence_end": 15, "ordinal": 0},
    }]}
    packets, _ = build_packets(
        selected, [PacketSource("logic.py", {1: "available"}, "fixed_commit", revision=SHA)],
        report=Path("report.md"), rng=random.Random(0), local_candidates=True,
    )
    sent, = packets[0]["data_block"]
    assert sent["target_evidence"] == [
        {"path": "logic.py", "start_line": 1, "end_line": 1, "excerpt_index": 1},
        {"path": "logic.py", "start_line": 3, "end_line": 3, "excerpt_index": None},
    ]


def test_local_packet_does_not_cover_a_target_line_clipped_at_packet_limit():
    selected = {"selected_count": 1, "deferred_count": 0, "items": [{
        "id": "clipped-target", "branch": "partial_range", "report_line": 1,
        "claim": "synthetic claim", "before": "", "after": "", "paths": ["logic.py"],
        "identifiers": [], "ranges": [{"path": "logic.py", "start": 1, "end": 1}],
        "target_evidence": [{"path": "logic.py", "start": 1, "end": 1}],
        "atom": {"check_type": "K1", "reason_code": "partial_range",
                 "sentence_start": 0, "sentence_end": 15, "ordinal": 0},
    }]}
    packets, _ = build_packets(
        selected, [PacketSource("logic.py", {1: "word " * 500}, "fixed_commit", revision=SHA)],
        report=Path("report.md"), rng=random.Random(0), local_candidates=True,
    )
    sent, = packets[0]["data_block"]
    assert sent["excerpts"][0]["text"].startswith("1: ")
    assert sent["target_evidence"] == [
        {"path": "logic.py", "start_line": 1, "end_line": 1, "excerpt_index": None},
    ]


def test_local_other_file_evidence_rejects_a_different_fixed_revision():
    report = "`logic.py:1` 함수 `moved_name` 정의가 있다."
    hit = {"path": "other.ts", "line": 2, "revision": SHA, "kind": "definition"}
    checked = {"claims": [claim("logic.py:1", "found_outside_scope", source={"repository_hits": [hit]})]}
    checked["claims"][0]["check_type"] = "K1"
    selected = select_candidates(report, checked, fixed_commit=True, local_candidates=True)
    packets, _ = build_packets(
        selected,
        [PacketSource("C:/synthetic/repo/logic.py", {1: "original"}, "fixed_commit", revision=SHA),
         PacketSource("C:/synthetic/repo/other.ts", {2: "wrong revision"}, "fixed_commit", revision="b" * 40),
         PacketSource("C:/synthetic/repo/other.ts", {2: "transcript substitute"}, "transcript_read")],
        report=Path("report.md"), rng=random.Random(0), local_candidates=True,
    )
    sent, = packets[0]["data_block"]
    assert sent["target_evidence"][0]["excerpt_index"] == 1
    assert sent["other_file_evidence"] == [{"path": "other.ts", "start_line": 2, "end_line": 2,
                                             "revision": SHA, "excerpt_index": None}]
    assert [excerpt["path"] for excerpt in sent["excerpts"]] == ["C:/synthetic/repo/logic.py"]
    assert "wrong revision" not in sent["excerpts"][0]["text"]
    assert "transcript substitute" not in sent["excerpts"][0]["text"]
