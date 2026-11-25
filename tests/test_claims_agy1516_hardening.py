"""Hardening regression tests for agy 1.5.16, doc-dir, #L links, and cutoffs.

All transcripts, paths, and reports are synthetic (``C:/sample/...`` or ``tmp_path``).
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from smtw.claims.check import run
from smtw.claims.extract import extract
from smtw.claims.model import Budget
from smtw.claims.transcripts import discover_agy, read_transcript
from tests.claims_support import agy_call, generic, make_case, view, write_transcript


def test_read_file_self_write_cannot_pass_unlinked_k4(tmp_path):
    target = "C:/sample/generated.py"
    report, transcript = make_case(
        tmp_path,
        "함수 `invented_helper()` 확인",
        [
            agy_call("write_file", Path=target, Content="def invented_helper():\n    return 1\n"),
            generic("ok"),
            agy_call("read_file", Path=target),
            generic("Showing lines 1-2 of 2\n1: def invented_helper():\n2:     return 1"),
        ],
    )
    result = run(report, agy=str(transcript))
    assert result["claims"][0]["verdict"] == "unverifiable"
    assert result["status"] == "INCOMPLETE"


def test_read_file_source_contents_do_not_trigger_error_or_pointer_missing(tmp_path):
    source_body = (
        "Showing lines 1-4 of 4\n"
        '1: status = {"status": "ERROR"}\n'
        '2: # note: steps/99/output.txt\n'
        '3: msg = "Output has been saved to: C:/sample/steps/99/output.txt"\n'
        "4: def ok(): return True"
    )
    report, transcript = make_case(
        tmp_path,
        "`sample.py` 함수 `ok()`",
        [
            agy_call("read_file", Path="C:/sample/sample.py"),
            generic(source_body),
        ],
    )
    tr = read_transcript(transcript, "agy", report, report.read_text(encoding="utf-8"), {}, Budget(10))
    assert tr.evidence and tr.evidence[0].control_only is True
    assert tr.evidence[0].problem == ""
    assert tr.evidence[0].problem_code == ""


def test_discover_agy_normalizes_crlf_line_endings(tmp_path):
    brain = tmp_path / "brain"
    report = tmp_path / "report.md"
    report.write_bytes("2건 중 1건\r\n".encode("utf-8"))
    rows = [
        agy_call("write_file", Path=str(report), Content="2건 중 1건\n"),
        generic("ok"),
    ]
    tx = write_transcript(brain / "conv-crlf/.system_generated/logs/transcript_full.jsonl", rows)
    found = discover_agy(report, report.read_text(encoding="utf-8"), brain, {}, Budget(10))
    assert found == [tx]
    assert run(report, agy_auto=True, brain_root=brain)["status"] == "PASS"


def test_doc_dir_verifies_plain_files_and_skips_protected_paths(tmp_path):
    doc_dir = tmp_path / "docs"
    doc_dir.mkdir()
    (doc_dir / "guide.md").write_text("alpha line\nbeta target phrase\n", encoding="utf-8")
    secret_dir = doc_dir / "customer-data"
    secret_dir.mkdir()
    (secret_dir / "leak.md").write_text("secret target phrase\n", encoding="utf-8")

    ok_report = tmp_path / "ok.md"
    ok_report.write_text('`guide.md:2` "beta target phrase"', encoding="utf-8")
    ok_res = run(ok_report, doc_dirs=[str(doc_dir)])
    assert ok_res["status"] == "PASS"
    assert all(c["verdict"] == "match" for c in ok_res["claims"])

    bad_report = tmp_path / "bad.md"
    bad_report.write_text('`guide.md:2` "completely absent quote"', encoding="utf-8")
    bad_res = run(bad_report, doc_dirs=[str(doc_dir)])
    assert bad_res["status"] == "FAIL"

    prot_report = tmp_path / "prot.md"
    prot_report.write_text('`customer-data/leak.md:1` "secret target phrase"', encoding="utf-8")
    prot_res = run(prot_report, doc_dirs=[str(doc_dir)])
    assert prot_res["status"] == "INCOMPLETE"
    assert prot_res["claims"][0]["verdict"] == "unverifiable"


def test_before_timestamp_cuts_off_later_checkpoints_and_reads(tmp_path):
    engine = "C:/sample/engine.py"
    report, transcript = make_case(
        tmp_path,
        "`engine.py` 함수 `valid_name`",
        [
            {"type": "PLANNER_RESPONSE", "created_at": "2026-01-01T10:00:00Z",
             "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": engine}}]},
            {"type": "GENERIC", "created_at": "2026-01-01T10:00:01Z", "status": "DONE",
             "content": f"File Path: `file:///{engine}`\nTotal Lines: 1\nShowing lines 1 to 1\n1: def valid_name(): pass"},
            {"type": "CHECKPOINT", "created_at": "2026-01-01T10:05:00Z", "step_index": 5, "status": "DONE", "content": "cp"},
            {"type": "PLANNER_RESPONSE", "created_at": "2026-01-01T10:06:00Z",
             "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": engine}}]},
            {"type": "GENERIC", "created_at": "2026-01-01T10:06:01Z", "status": "DONE",
             "content": f"File Path: `file:///{engine}`\nTotal Lines: 1\nShowing lines 1 to 1\n1: def valid_name(): pass"},
        ],
    )
    before_cp = run(report, agy=str(transcript), before_timestamp="2026-01-01T10:01:00Z")
    assert before_cp["compaction"]["count"] == 0
    at_cp = run(report, agy=str(transcript), before_timestamp="2026-01-01T10:05:30Z")
    assert at_cp["compaction"]["stale_claim_lines"] == [1]
    after_reread = run(report, agy=str(transcript), before_timestamp="2026-01-01T10:07:00Z")
    assert after_reread["compaction"]["stale_claim_lines"] == []


ENGINE = "C:/sample/engine.py"
ENGINE_VIEW = f"File Path: `file:///{ENGINE}`\nTotal Lines: 1\nShowing lines 1 to 1\n1: def valid_name(): pass"


def _engine_rows(call_at=None, answer_at=None):
    call = {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": ENGINE}}]}
    answer = {"type": "GENERIC", "status": "DONE", "content": ENGINE_VIEW}
    if call_at:
        call["created_at"] = call_at
    if answer_at:
        answer["created_at"] = answer_at
    return [call, answer]


def test_before_timestamp_excludes_response_after_cutoff(tmp_path):
    report, transcript = make_case(tmp_path, "`engine.py` 함수 `valid_name`",
                                   _engine_rows("2026-01-01T09:59:59Z", "2026-01-01T10:00:01Z"))
    late = run(report, agy=str(transcript), before_timestamp="2026-01-01T10:00:00Z")
    assert late["claims"][0]["verdict"] != "match"
    assert late["status"] != "PASS"
    in_time = run(report, agy=str(transcript), before_timestamp="2026-01-01T10:00:01Z")
    assert in_time["claims"][0]["verdict"] == "match"


def test_before_timestamp_without_any_timestamp_uses_no_evidence(tmp_path):
    report, transcript = make_case(tmp_path, "`engine.py` 함수 `valid_name`", _engine_rows())
    bounded = run(report, agy=str(transcript), before_timestamp="2026-01-01T10:00:00Z")
    assert bounded["claims"][0]["verdict"] != "match"
    assert bounded["status"] != "PASS"
    tr = read_transcript(transcript, "agy", report, report.read_text(encoding="utf-8"), {}, Budget(10),
                         before_timestamp="2026-01-01T10:00:00Z")
    assert all(e.after_report for e in tr.evidence)
    assert any("--before-timestamp" in w for w in tr.warnings)
    assert run(report, agy=str(transcript))["claims"][0]["verdict"] == "match"


def _link(link: Path, target: Path) -> None:
    """Symlink, or a directory junction on Windows without symlink rights; else skip."""
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt" and target.is_dir():
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return
    pytest.skip("symbolic links are not available here")


def test_doc_dir_ignores_directory_link_outside_root(tmp_path):
    docs, outside = tmp_path / "docs", tmp_path / "outside"
    docs.mkdir()
    outside.mkdir()
    (outside / "plans.md").write_text("alpha line\nbeta target phrase\n", encoding="utf-8")
    (docs / "guide.md").write_text("alpha line\nbeta target phrase\n", encoding="utf-8")
    _link(docs / "jdir", outside)
    escaped = tmp_path / "escaped.md"
    escaped.write_text('`jdir/plans.md:2` "beta target phrase"', encoding="utf-8")
    res = run(escaped, doc_dirs=[str(docs)])
    assert res["claims"][0]["verdict"] != "match"
    assert res["status"] != "PASS"
    inside = tmp_path / "inside.md"
    inside.write_text('`guide.md:2` "beta target phrase"', encoding="utf-8")
    assert run(inside, doc_dirs=[str(docs)])["status"] == "PASS"


def test_before_timestamp_late_response_is_no_evidence_for_current_claims(tmp_path):
    report, transcript = make_case(tmp_path, "현재 HEAD `engine.py` 함수 `valid_name`",
                                   _engine_rows("2026-01-01T09:59:59Z", "2026-01-01T10:00:01Z"))
    late = run(report, agy=str(transcript), before_timestamp="2026-01-01T10:00:00Z")
    assert late["status"] != "PASS"
    assert all(c["verdict"] != "match" for c in late["claims"])
    in_time = run(report, agy=str(transcript), before_timestamp="2026-01-01T10:00:01Z")
    assert in_time["status"] == "PASS"
    (tmp_path / "untimed").mkdir()
    untimed_report, untimed = make_case(tmp_path / "untimed", "현재 HEAD `engine.py` 함수 `valid_name`", _engine_rows())
    bounded = run(untimed_report, agy=str(untimed), before_timestamp="2026-01-01T10:00:00Z")
    assert bounded["status"] != "PASS"
    assert all(c["verdict"] != "match" for c in bounded["claims"])


def test_before_timestamp_stops_at_first_later_step(tmp_path):
    rows = _engine_rows("2026-01-01T09:59:59Z", "2026-01-01T10:00:01Z")
    rows.append({"type": "EPHEMERAL_MESSAGE", "source": "SYSTEM_SDK", "status": "DONE",
                 "created_at": "2026-01-01T09:00:00Z", "content": "metadata with an earlier time"})
    report, transcript = make_case(tmp_path, "`engine.py` 함수 `valid_name`", rows)
    res = run(report, agy=str(transcript), before_timestamp="2026-01-01T10:00:00Z")
    assert res["claims"][0]["verdict"] != "match"
    assert res["status"] != "PASS"


def test_before_timestamp_late_reread_does_not_clear_compaction(tmp_path):
    engine = "C:/sample/engine.py"
    body = f"File Path: `file:///{engine}`\nTotal Lines: 1\nShowing lines 1 to 1\n1: def valid_name(): pass"
    report, transcript = make_case(
        tmp_path,
        "`engine.py` 함수 `valid_name`",
        [
            {"type": "PLANNER_RESPONSE", "created_at": "2026-01-01T10:00:00Z",
             "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": engine}}]},
            {"type": "GENERIC", "created_at": "2026-01-01T10:00:01Z", "status": "DONE", "content": body},
            {"type": "CHECKPOINT", "created_at": "2026-01-01T10:05:00Z", "step_index": 5, "status": "DONE", "content": "cp"},
            {"type": "PLANNER_RESPONSE", "created_at": "2026-01-01T10:06:00Z",
             "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": engine}}]},
            {"type": "GENERIC", "created_at": "2026-01-01T10:08:00Z", "status": "DONE", "content": body},
        ],
    )
    late = run(report, agy=str(transcript), before_timestamp="2026-01-01T10:07:00Z")
    assert late["compaction"]["stale_claim_lines"] == [1]
    answered = run(report, agy=str(transcript), before_timestamp="2026-01-01T10:09:00Z")
    assert answered["compaction"]["stale_claim_lines"] == []


def test_doc_dir_never_walks_link_to_protected_folder(tmp_path, monkeypatch):
    import smtw.claims.sources as sources_module

    docs = tmp_path / "docs"
    secret = docs / "customer-data"
    secret.mkdir(parents=True)
    (secret / "leak.md").write_text("alpha line\nsecret target phrase\n", encoding="utf-8")
    (docs / "guide.md").write_text("alpha line\nbeta target phrase\n", encoding="utf-8")
    _link(docs / "neutral", secret)
    walked = []
    real_walk = sources_module.os.walk

    def spy(top, *args, **kwargs):
        for cur, dirs, files in real_walk(top, *args, **kwargs):
            walked.append(os.path.realpath(cur))
            yield cur, dirs, files

    monkeypatch.setattr(sources_module.os, "walk", spy)
    report = tmp_path / "neutral.md"
    report.write_text('`neutral/leak.md:2` "secret target phrase"', encoding="utf-8")
    res = run(report, doc_dirs=[str(docs)])
    assert res["claims"][0]["verdict"] != "match"
    assert walked, "the document folder itself is walked"
    assert all(os.path.realpath(secret) != w for w in walked)


def test_discover_agy_prefilter_ignores_filename_case(tmp_path):
    brain = tmp_path / "brain"
    report = tmp_path / "REPORT.MD"
    report.write_bytes("old text\n".encode("utf-8"))
    rows = [
        agy_call("write_file", Path=str(report), Content="old text\n"),
        generic("ok"),
        agy_call("write_file", Path=str(report.with_name("Report.md")), Content="new text\n"),
        generic("ok"),
    ]
    write_transcript(brain / "conv-case/.system_generated/logs/transcript_full.jsonl", rows)
    assert discover_agy(report, report.read_text(encoding="utf-8"), brain, {}, Budget(10)) == []
    rows_old_last = rows[2:] + rows[:2]
    tx = write_transcript(brain / "conv-case/.system_generated/logs/transcript_full.jsonl", rows_old_last)
    assert discover_agy(report, report.read_text(encoding="utf-8"), brain, {}, Budget(10)) == [tx]


def test_doc_dir_ignores_file_link_outside_root(tmp_path):
    docs, outside = tmp_path / "docs", tmp_path / "outside"
    docs.mkdir()
    outside.mkdir()
    (outside / "plans.md").write_text("alpha line\nbeta target phrase\n", encoding="utf-8")
    try:
        (docs / "link.md").symlink_to(outside / "plans.md")
    except (OSError, NotImplementedError):
        pytest.skip("file symbolic links are not available here")
    report = tmp_path / "linked.md"
    report.write_text('`link.md:2` "beta target phrase"', encoding="utf-8")
    res = run(report, doc_dirs=[str(docs)])
    assert res["claims"][0]["verdict"] != "match"
    assert res["status"] != "PASS"


def test_hint_read_batch_survives_sibling_command_stdout_error_line(tmp_path):
    engine = "C:/sample/engine.py"
    rows = [
        agy_call("view_file", AbsolutePath=engine),
        view(engine, "def valid_name(): pass"),
        {"type": "CHECKPOINT", "step_index": 3, "status": "DONE", "content": "cp"},
        {
            "type": "PLANNER_RESPONSE",
            "tool_calls": [
                {"name": "read_file", "args": {"Path": engine}},
                {"name": "run_command", "args": {"CommandLine": "git status"}},
            ],
        },
        generic("Showing lines 1-1 of 1\n1: def valid_name(): pass"),
        generic("Command exited with code 1.\nError: something printed by sibling command"),
    ]
    report, transcript = make_case(tmp_path, "`engine.py` 함수 `valid_name`", rows)
    result = run(report, agy=str(transcript))
    assert result["compaction"]["stale_claim_lines"] == []


def test_file_uri_hash_line_links_extract_cleanly(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text("\n".join(f"line_{i} = {i}" for i in range(1, 25)) + "\n", encoding="utf-8")
    uri = target.as_uri()
    line = f'참조: [mod.py]({uri}#L10-L12) "line_10 = 10"'
    claims, _ = extract(line, Budget(10))
    k1 = [c for c in claims if c.check_type == "K1"]
    k5 = [c for c in claims if c.check_type == "K5"]
    assert len(k1) == 1 and k1[0].start == 10 and k1[0].end == 12
    assert k1[0].quote == "line_10 = 10"
    assert len(k5) == 1 and k5[0].binding_candidates == () and k5[0].path == uri

    report = tmp_path / "rep.md"
    report.write_text(f'참조: [mod.py]({uri}#L10-L12) "wrong_quote_text"', encoding="utf-8")
    res = run(report, doc_dirs=[str(tmp_path)])
    assert res["status"] == "FAIL"

    # Web URLs with #L fragments must NOT produce K1 file citations.
    web_claims, _ = extract("참고: https://example.invalid/org/repo/blob/main/app.py#L10-L20", Budget(10))
    assert not [c for c in web_claims if c.check_type == "K1"]


def test_compaction_all_cited_tracks_commit_backed_claims(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def real_fn():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=s", "-c", "user.email=s@e.invalid", "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=s", "-c", "user.email=s@e.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"], check=True)
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    report, transcript = make_case(
        tmp_path,
        "`app.py:1` 함수 `real_fn`",
        [
            agy_call("read_file", Path=str(repo / "app.py")),
            generic("Showing lines 1-2 of 2\n1: def real_fn():\n2:     return 1"),
            {"type": "CHECKPOINT", "step_index": 2, "status": "DONE", "content": "cp"},
        ],
    )
    res = run(report, agy=str(transcript), repos=[str(repo)], commits={str(repo): sha})
    assert res["status"] == "PASS"
    assert res["compaction"]["stale_claim_lines"] == []
    assert res["compaction_all_cited"]["stale_claim_lines"] == [1]
    assert res["compaction_all_cited"]["stale_files"] == ["app.py"]


def test_mcp_wiki_search_unwraps_as_non_url_evidence(tmp_path):
    report, transcript = make_case(
        tmp_path,
        "문서 확인: https://example.invalid/should-not-match",
        [
            agy_call("call_mcp_tool", ServerName="wiki-search", ToolName="search_wiki", Arguments={"query": "smtw"}),
            generic("1. guide (score 0.9)\npath: C:/sample/guide.md"),
        ],
    )
    tr = read_transcript(transcript, "agy", report, report.read_text(encoding="utf-8"), {}, Budget(10))
    assert any(e.tool == "wiki-search.search_wiki" and not e.url for e in tr.evidence)
    res = run(report, agy=str(transcript))
    k8 = [c for c in res["claims"] if c["check_type"] == "K8"]
    assert k8 and k8[0]["verdict"] == "absent"


@pytest.mark.parametrize("bad_ts", ["not-a-timestamp"])
def test_invalid_before_timestamp_raises(tmp_path, bad_ts):
    report, transcript = make_case(tmp_path, "2건 중 1건", [agy_call("view_file", AbsolutePath="C:/sample/a.py"), view("C:/sample/a.py", "x")])
    with pytest.raises(ValueError):
        read_transcript(transcript, "agy", report, report.read_text(encoding="utf-8"), {}, Budget(10), before_timestamp=bad_ts)


@pytest.mark.parametrize("ensure_ascii", [False, True])
def test_discover_agy_korean_filename_utf8_and_json_escaped(tmp_path, ensure_ascii):
    import json

    brain = tmp_path / "brain"
    report = tmp_path / "보고서.md"
    report.write_bytes("2건 중 1건\n".encode("utf-8"))
    rows = [
        agy_call("write_file", Path=str(report), Content="2건 중 1건\n"),
        generic("ok"),
    ]
    tx = brain / "conv-kr/.system_generated/logs/transcript_full.jsonl"
    tx.parent.mkdir(parents=True)
    tx.write_text(
        "".join(json.dumps(r, ensure_ascii=ensure_ascii) + "\n" for r in rows),
        encoding="utf-8",
    )
    found = discover_agy(report, report.read_text(encoding="utf-8"), brain, {}, Budget(10))
    assert found == [tx]
    assert run(report, agy_auto=True, brain_root=brain)["status"] == "PASS"
