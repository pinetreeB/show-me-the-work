"""Synthetic packet-only fixed-blob provenance and checker isolation."""
import json

import pytest

from smtw.claims.model import Budget
from smtw.claims.packets import transcript_sources
from smtw.claims.transcripts import Call, read_transcript
from smtw.claims.packet_reads import parse_packet_result, simple_read_path
from tests.claims_support import agy_call, generic, make_case

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
    evidence = read_transcript(transcript, kind, report, text, {}, Budget(10),
                               result_parser=parse_packet_result).evidence
    assert len(evidence) == 1
    e = evidence[0]
    assert e.path.casefold() == PATH.casefold() and e.revision == REV and e.lines == {1: "VALUE = 2"}
    assert e.complete == ("Select-Object" not in tail)
    sources = transcript_sources(transcript, kind, report, text, {}, Budget(10))
    assert len(sources) == 1 and sources[0].revision == REV
    assert sources[0].lines == {1: "VALUE = 2"}






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




def test_rejected_git_read_exposes_no_packet_body_or_version(tmp_path):
    call = Call("run_command", {"CommandLine": git_command(" | custom-filter"),
                               "Cwd": "C:/synthetic/project"}, 1, "synthetic-time")
    output = "File Path: other.py\nTotal Lines: 999\n1: EXCLUDED_MARKER\nTask logs are available at: missing.log"
    evidence = parse_packet_result(call, output, "synthetic-origin", tmp_path, Budget(10))
    assert evidence
    for e in evidence:
        assert e.control_only and not e.path and not e.revision
        assert not e.text and not e.lines and not e.counts and not e.aux_path
        assert e.total_bytes is None and e.total_lines is None and not e.complete


def test_git_source_number_prefix_is_literal_content(tmp_path):
    e, = parse_packet_result(Call("run_command", {"CommandLine":git_command()}, 1, ""),
                      "17: literal source text", "synthetic-origin", tmp_path, Budget(10))
    assert e.lines == {1: "17: literal source text"}
