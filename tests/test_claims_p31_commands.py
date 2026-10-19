"""Closed shell-read provenance; all command text and output are synthetic."""
import json
from pathlib import Path

import pytest

from smtw.claims.assist import select_candidates
from smtw.claims.model import Budget
from smtw.claims.packets import build_packets, transcript_sources
from smtw.claims.transcripts import Call, call_paths, simple_read_path

ROOT = "C:/synthetic/tasks/example"
SECOND = [".env", "customer-data/list", "customer-data/list.xlsx", "creds.pem",
          "id_rsa", "credentials", "x.ini", "x.cfg", "x.conf", "x.html", "x.eml",
          "x.vcf", "x.rtf", "x.pdf", "x.key", ".secrets/token", "legal/nda",
          "contracts/c1", "x.env", "tokens.dat", "x.sqlite", "x.xlsx", "x.csv",
          "Makefile", "x.json", "x.MD", "x.markdown"]
COMPOUND = [f"cat {ROOT}/card.md {ROOT}/{second}" for second in SECOND]
for second in (".env", "customer-data/list", "x.ini"):
    COMPOUND.extend([
        f"Get-Content {ROOT}/card.md; Get-Content {ROOT}/{second}",
        f"cd {ROOT}; cat {ROOT}/card.md {second}",
        f"cat {ROOT}/card.md ../{second}",
        f"cat {ROOT}/{second} {ROOT}/card.md",
    ])
COMPOUND += [
    f"cat {ROOT}/card.md; ls {ROOT}/customer-data",
    f"cat {ROOT}/card.md; Get-ChildItem {ROOT}/customer-data",
    f"cat {ROOT}/card.md && dir {ROOT}",
    f"cat {ROOT}/card.md; printenv",
    f"cat {ROOT}/card.md; whoami",
    f"cat {ROOT}/card.md; curl http://localhost:8080/admin",
    f"cat {ROOT}/card.md | tee {ROOT}/out.txt",
    f'cat {ROOT}/card.md; python -c "print(open(\'.env\').read())"',
    f"cat {ROOT}/card.md; sqlite3 customers.db 'select * from c'",
]
assert len(COMPOUND) == 48


def sources_for(tmp_path, kind, command, body):
    report = tmp_path / "report.md"
    text = "IdentAaa returns 7 ms.\n참고 문서: card.md\n"
    report.write_text(text, encoding="utf-8")
    if kind == "agy":
        rows = [{"type": "PLANNER_RESPONSE", "tool_calls": [
            {"name": "run_command", "args": {"CommandLine": command}}]},
            {"type": "GENERIC", "status": "DONE", "content": body}]
    else:
        rows = [{"type": "response_item", "payload": {
            "type": "function_call", "name": "exec_command", "call_id": "read-example",
            "arguments": json.dumps({"cmd": command})}},
            {"type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "read-example",
                "output": json.dumps({"output": body, "exit_code": 0})}}]
    transcript = tmp_path / (kind + ".jsonl")
    transcript.write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    sources = transcript_sources(transcript, kind, report, text, {}, Budget(30))
    selected = select_candidates(text, {"claims": []})
    packets, summary = build_packets(selected, sources, report=report)
    return sources, packets, summary


@pytest.mark.parametrize("kind", ["agy", "codex"])
@pytest.mark.parametrize("command", COMPOUND, ids=[f"compound-{i:02}" for i in range(48)])
def test_r12_compound_variants_drop_entire_file_output(tmp_path, kind, command):
    sources, packets, summary = sources_for(tmp_path, kind, command,
        "IdentAaa returns seven ms. APPROVED_MARKER\nEXCLUDED_MARKER customer row\n")
    assert not sources and not packets and summary["sent_count"] == 0
    assert call_paths(Call('exec_command', {'cmd': command}, 1, '')) == []


@pytest.mark.parametrize("kind", ["agy", "codex"])
@pytest.mark.parametrize("reader", ["cat", "Get-Content -LiteralPath", "type"])
def test_single_simple_file_read_still_produces_excerpt(tmp_path, kind, reader):
    sources, packets, summary = sources_for(tmp_path, kind, f"{reader} {ROOT}/card.md",
                                           "IdentAaa returns seven ms. APPROVED_MARKER\n")
    assert len(sources) == 1 and sources[0].path == ROOT + "/card.md"
    assert summary["sent_count"] and "APPROVED_MARKER" in json.dumps(packets)


@pytest.mark.parametrize("suffix", ["; printenv", " && whoami", " || whoami",
                                    "\nprintenv", " | head -5", " $(printenv)", " " + chr(96) + "printenv" + chr(96)])
@pytest.mark.parametrize("header", ["", "File Path: C:/synthetic/tasks/example/card.md\nTotal Lines: 1\n"])
def test_output_headers_cannot_restore_ambiguous_shell_binding(tmp_path, suffix, header):
    sources, packets, _ = sources_for(tmp_path, "codex", f"cat {ROOT}/card.md" + suffix,
                                     header + "1: IdentAaa returns seven ms. EXCLUDED_MARKER\n")
    assert not sources and not packets


def test_all_command_tokens_are_checked(monkeypatch):
    from smtw.claims import packets
    visited = []
    monkeypatch.setattr(packets, "hard_excluded", lambda token: visited.append(token) or False)
    assert simple_read_path("Get-Content -LiteralPath card.md -Raw") == "card.md"
    assert set(["Get-Content", "-LiteralPath", "card.md", "-Raw"]) <= set(visited)


def test_explicit_path_argument_cannot_bypass_command_grammar():
    call = Call("exec_command", {"cmd": "cat card.md .env", "path": "card.md"}, 1, "")
    assert call_paths(call) == []


@pytest.mark.parametrize("command", [
    'cat "card.md" "other"', "cat *.md", "Get-Content card.md,other",
    'python -c "print(open(\'card.md\').read())"', "cat card.md > result",
    "Get-Content -LiteralPath", "Get-Content -Unknown card.md", "cat card.md#comment",
])
def test_unknown_or_dynamic_read_forms_are_not_file_evidence(command):
    assert not simple_read_path(command)


@pytest.mark.parametrize("command", ["(Get-Content card.md).Count", "@(Get-Content card.md).Length",
                                    "wc -l card.md", "stat -c %s card.md"])
def test_measurement_output_never_becomes_file_excerpt(tmp_path, command):
    sources, packets, _ = sources_for(tmp_path, "codex", command, "7\n")
    assert not sources and not packets
