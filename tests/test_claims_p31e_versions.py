"""Synthetic version provenance and no-output recovery for rejected shell reads."""
import json

import pytest

from smtw.claims.check import run
from smtw.claims.model import Budget
from smtw.claims.packets import transcript_sources
from smtw.claims.transcripts import Call, parse_result, read_transcript, simple_read_path
from tests.claims_support import agy_call, generic, make_case, view

PATH = "C:/synthetic/project/widget.py"
REV = "abcdef1234567890"
TAILS = ["", " | Out-String", " 2>&1", " 2>&1 | Out-String",
         " | Select-Object -First 1", " | Select-Object -First 2 | Out-String"]


def quoted(value):
    return chr(96) + value + chr(96)


def git_command(tail="", root=False):
    return (f'git -C "C:/synthetic/project" show "{REV}:widget.py"' if root
            else f"git show {REV}:widget.py") + tail


def git_rows(tail="", body="VALUE = 2"):
    return [agy_call("run_command", CommandLine=git_command(tail),
                     Cwd="C:/synthetic/project"), generic(body)]


@pytest.mark.parametrize("tail", TAILS)
@pytest.mark.parametrize("kind", ["agy", "codex"])
def test_fixed_blob_display_tail_preserves_revision_and_excerpt(tmp_path, tail, kind):
    text = quoted("widget.py:1") + " " + quoted("VALUE = 2")
    command = git_command(tail, root=True)
    if kind == "agy":
        rows = [agy_call("run_command", CommandLine=command), generic("VALUE = 2")]
    else:
        rows = [
            {"type": "response_item", "payload": {"type": "function_call",
             "name": "exec_command", "call_id": "blob",
             "arguments": json.dumps({"cmd": command})}},
            {"type": "response_item", "payload": {"type": "function_call_output",
             "call_id": "blob", "output": json.dumps({"output": "VALUE = 2", "exit_code": 0})}},
        ]
    report, transcript = make_case(tmp_path, text, rows)
    evidence = read_transcript(transcript, kind, report, text, {}, Budget(10)).evidence
    assert len(evidence) == 1
    e = evidence[0]
    assert e.path.casefold() == PATH.casefold() and e.revision == REV and e.lines == {1: "VALUE = 2"}
    assert e.complete == ("Select-Object" not in tail)
    sources = transcript_sources(transcript, kind, report, text, {}, Budget(10))
    assert len(sources) == 1 and sources[0].revision == REV
    assert sources[0].lines == {1: "VALUE = 2"}


@pytest.mark.parametrize("tail", TAILS[:4])
def test_two_complete_versions_do_not_falsely_reject_commit_quote(tmp_path, tail):
    rows = [agy_call("view_file", AbsolutePath=PATH), view(PATH, "VALUE = 1"),
            *git_rows(tail)]
    report, transcript = make_case(tmp_path, quoted("widget.py:1") + " " + quoted("VALUE = 2"), rows)
    result = run(report, agy=str(transcript))
    assert result["claims"]
    assert not any(c["verdict"] in {"mismatch", "absent"} for c in result["claims"])
    assert any(c["reason_code"] == "observation_conflict" for c in result["claims"])


@pytest.mark.parametrize("tail", TAILS[:4])
def test_invented_quote_absent_from_both_complete_versions_still_fails(tmp_path, tail):
    rows = [agy_call("view_file", AbsolutePath=PATH), view(PATH, "VALUE = 1"),
            *git_rows(tail)]
    report, transcript = make_case(tmp_path, quoted("widget.py:1") + ' "invented source phrase"', rows)
    result = run(report, agy=str(transcript))
    assert result["status"] == "FAIL"
    assert any(c["reason_code"] == "quote_mismatch" and c["verdict"] == "mismatch"
               for c in result["claims"])


@pytest.mark.parametrize("tail", [
    " | Get-Content .env", " | Out-String; cat other.md", " | Out-String other.md",
    " | Select-Object -First 1 other.md", " | Select-Object -First 1; printenv",
    " | Select-Object -First 1 -ExpandProperty x", " | Out-String -Width 50",
    " | python -c 'print(1)'", " && cat other.md", " > output.txt",
    " 2>&1; whoami", " | Out-String\ncat other.md", " | Select-Object -First $count",
])
def test_unlisted_git_tails_remain_non_sources(tmp_path, tail):
    command = git_command(tail)
    assert not simple_read_path(command)
    text = quoted("widget.py:1") + " " + quoted("EXCLUDED_MARKER")
    report, transcript = make_case(tmp_path, text, [
        agy_call("run_command", CommandLine=command), generic("EXCLUDED_MARKER")])
    assert not transcript_sources(transcript, "agy", report, text, {}, Budget(10))


@pytest.mark.parametrize("command", [
    f"git show {REV}:.env", f"git show {REV}:customer-data/list.md",
    f"git -C customer-data show {REV}:widget.py",
    f"git show {REV}:widget.py {REV}:other.py",
    f'git show "{REV}:$(whoami).py"', "git show HEAD:widget.py",
])
def test_git_sensitive_or_unfixed_operands_rejected(command):
    assert not simple_read_path(command)


@pytest.mark.parametrize("claim", [quoted("widget.py")+"는 99줄", quoted("widget.py")+"는 99 bytes"])
def test_opaque_python_read_blocks_wrong_fixed_snapshot_mismatch(tmp_path, claim):
    command = 'python -c "with open(\'widget.py\') as f: print(len(f.read()))"'
    report, transcript = make_case(tmp_path, claim, [
        agy_call("run_command", CommandLine=command), generic("99")])
    source = tmp_path / "fixed.py"
    source.write_text("first\nsecond\n", encoding="utf-8")
    result = run(report, agy=str(transcript), sources={"widget.py": str(source)})
    numeric = [c for c in result["claims"] if c["check_type"] in {"K2", "K6"}]
    assert numeric
    assert all(c["verdict"] == "unverifiable" and c["reason_code"] == "opaque_version"
               for c in numeric)
    assert all(len(c["source"]["observations"]) == 2 for c in numeric)


def test_rejected_git_read_keeps_path_and_revision_without_any_output(tmp_path):
    call = Call("run_command", {"CommandLine": git_command(" | custom-filter"),
                               "Cwd": "C:/synthetic/project"}, 1, "synthetic-time")
    output = "File Path: other.py\nTotal Lines: 999\n1: EXCLUDED_MARKER\nTask logs are available at: missing.log"
    evidence = parse_result(call, output, "synthetic-origin", tmp_path, Budget(10))
    opaque = [e for e in evidence if e.problem_code == "opaque_version"]
    assert len(opaque) == 1
    e = opaque[0]
    assert e.path == "widget.py" and e.revision == REV
    assert not e.text and not e.lines and not e.counts and not e.aux_path and not e.command
    assert e.total_bytes is None and e.total_lines is None and not e.complete


def test_opaque_observation_prevents_range_fallback_from_discarding_it(tmp_path):
    report, transcript = make_case(tmp_path, "widget.py:9 " + quoted("VALUE = 2"), [
        agy_call("run_command", CommandLine="cat widget.py | custom-filter"),
        generic("EXCLUDED_MARKER")])
    source = tmp_path / "fixed.py"
    source.write_text("VALUE = 1", encoding="utf-8")
    result = run(report, agy=str(transcript), sources={"widget.py": str(source)})
    k1 = [c for c in result["claims"] if c["check_type"] == "K1"]
    assert k1 and all(c["verdict"] == "unverifiable" and c["reason_code"] == "opaque_version" for c in k1)


def test_unrelated_opaque_path_does_not_hide_real_failure(tmp_path):
    report, transcript = make_case(tmp_path, quoted("widget.py:1") + " " + quoted("VALUE = 999"), [
        agy_call("view_file", AbsolutePath=PATH), view(PATH, "VALUE = 1"),
        agy_call("run_command", CommandLine="cat other.py | custom-filter"), generic("ignored")])
    assert run(report, agy=str(transcript))["status"] == "FAIL"


def test_sensitive_opaque_hint_is_not_recorded(tmp_path):
    evidence = parse_result(Call("run_command", {"CommandLine":
        "cat widget.py customer-data/list.md"}, 1, ""), "EXCLUDED_MARKER",
        "synthetic-origin", tmp_path, Budget(10))
    opaque = [e for e in evidence if e.problem_code == "opaque_version"]
    assert [e.path for e in opaque] == ["widget.py"]
    assert all(not e.text and not e.lines and not e.aux_path for e in opaque)


def test_git_source_number_prefix_is_literal_content(tmp_path):
    e, = parse_result(Call("run_command", {"CommandLine":git_command()}, 1, ""),
                      "17: literal source text", "synthetic-origin", tmp_path, Budget(10))
    assert e.lines == {1: "17: literal source text"}

def test_invented_entity_value_absent_from_both_complete_versions_still_fails(tmp_path):
    rows = [agy_call("view_file", AbsolutePath=PATH), view(PATH, "VALUE = 1"), *git_rows()]
    report, transcript = make_case(tmp_path, "widget.py:1 " + quoted("VALUE = 999"), rows)
    result = run(report, agy=str(transcript))
    numeric = [c for c in result["claims"] if c["check_type"] == "K6"]
    assert numeric and all(c["verdict"] == "mismatch" for c in numeric)
    assert result["status"] == "FAIL"
