"""The outbound source policy must not change the deterministic checker."""
import json

import pytest

from smtw.claims.check import run
from smtw.claims.model import Budget
from smtw.claims.packets import transcript_sources
from smtw.claims.transcripts import read_transcript
from tests.claims_support import agy_call, generic, make_case, view


def decision(result):
    return {key: result[key] for key in ("status", "summary", "verdict_reason_counts", "claims")}


@pytest.mark.parametrize("command", [
    "cat C:/synthetic/widget.py other.ini",
    "Get-Content C:/synthetic/widget.py | Select-Object -Skip 0 -First 1",
])
def test_rejected_packet_output_still_has_legacy_checker_evidence(tmp_path, command):
    text = chr(96)+"widget.py:1"+chr(96)+' "present source phrase"'
    report, transcript = make_case(tmp_path, text, [
        agy_call("run_command", CommandLine=command),
        generic("line 1: present source phrase"),
    ])
    before = run(report, agy=str(transcript))
    k1 = [c for c in before["claims"] if c["check_type"] == "K1"]
    assert k1 and k1[0]["verdict"] == "match"
    assert not transcript_sources(transcript, "agy", report, text, {}, Budget(10))
    assert decision(before) == decision(run(report, agy=str(transcript)))
    evidence = read_transcript(transcript, "agy", report, text, {}, Budget(10)).evidence
    assert evidence and not any(e.problem_code == "opaque_version" for e in evidence)


def test_packet_policy_cannot_be_called_by_check(monkeypatch, tmp_path):
    from smtw.claims import packet_reads
    def forbidden(*args, **kwargs):
        raise AssertionError("packet policy called by checker")
    monkeypatch.setattr(packet_reads, "simple_read_path", forbidden)
    report, transcript = make_case(tmp_path, chr(96)+"widget.py:1"+chr(96)+' "missing phrase"', [
        agy_call("view_file", AbsolutePath="C:/synthetic/widget.py"),
        view("C:/synthetic/widget.py", "present source phrase"),
    ])
    assert run(report, agy=str(transcript))["status"] == "FAIL"


def test_python_measurement_retains_original_observation_without_opaque_version(tmp_path):
    text = chr(96)+"widget.py"+chr(96)+"는 99줄"
    report, transcript = make_case(tmp_path, text, [
        agy_call("run_command", CommandLine='python -c "with open(\'widget.py\') as f: print(len(f.read()))"'),
        generic("99"),
    ])
    source = tmp_path/"fixed.py"
    source.write_text("first\nsecond\n", encoding="utf-8")
    result = run(report, agy=str(transcript), sources={"widget.py":str(source)})
    assert all(c["reason_code"] != "opaque_version" for c in result["claims"])
    assert not transcript_sources(transcript,"agy",report,text,{},Budget(10))
