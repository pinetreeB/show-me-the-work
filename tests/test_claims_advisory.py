from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from smtw.claims.advisory import (
    load_sent_items, read_json, render_advisory, render_with_advisory,
    validate_judgments, verify_files, verify_judgments,
)
from smtw.claims.check import run
from smtw.claims.packets import write_packets
from tests.test_claims_packets import candidate, packetize, source

FIXTURE = Path(__file__).parent / "fixtures" / "claims-assist" / "judge.json"


def sent_packets(tmp_path, text="LIMIT = 7\nalpha content line\nomega content line"):
    judgments = read_json(FIXTURE)
    packets, summary = packetize([candidate(id=row["id"]) for row in judgments], [source(text)])
    folder = tmp_path / "packets"
    write_packets(folder, packets, summary)
    return folder


def test_handwritten_fixture_quote_reverification_and_display(tmp_path):
    folder = sent_packets(tmp_path)
    result = verify_files(FIXTURE, folder)
    assert [r["id"] for r in result["items"]] == ["contradicted", "supported"]
    assert result["counts"] == {"supported": 1, "contradicted": 1, "not_enough_evidence": 3,
                                "quote_verified": 2, "quote_not_in_excerpt": 1, "missing_quote_or_ref": 1}
    rendered = render_advisory(result)
    assert rendered.startswith("LLM 참고 표시 — 판정 아님")
    assert rendered.index("[contradicted") < rendered.index("[supported")
    assert '<span style="color:gray">' in rendered
    assert "OUTSIDE" not in rendered and "발췌 밖 수동" not in rendered
    assert "인용 누락 수동" not in rendered and "근거 부족 수동" not in rendered


def test_only_sent_redacted_excerpts_and_ref_are_used(tmp_path):
    folder = sent_packets(tmp_path, "password=synthetic\nvisible line")
    (tmp_path / "not-sent.py").write_text("PRIVATE = 9", encoding="utf-8")
    items = load_sent_items(folder)
    row = {"id": "supported", "verdict": "supported", "rationale": ""}
    for quote, ref in [("password=synthetic", 1), ("PRIVATE = 9", 1), ("visible line", 2), ("1", 1)]:
        result = verify_judgments([{**row, "evidence_quote": quote, "evidence_ref": ref}], items)
        assert not result["items"] and result["counts"]["quote_not_in_excerpt"] == 1
    result = verify_judgments([{**row, "evidence_quote": "[REDACTED:password]", "evidence_ref": 1}], items)
    assert result["counts"]["quote_verified"] == 0
    assert result["counts"]["quote_not_in_excerpt"] == 1


@pytest.mark.parametrize("quote,verified", [
    ("**alpha content line**", True),
    ("alpha content line ... omega content line", True),
    ("omega content line ... alpha content line", False),
    ("alpha ... omega", False),
    ("LIMIT = 8", False),
])
def test_existing_k5_normalization_and_omission_contract(tmp_path, quote, verified):
    items = load_sent_items(sent_packets(tmp_path))
    result = verify_judgments([{"id": "supported", "verdict": "supported", "evidence_quote": quote,
                                "evidence_ref": 1, "rationale": ""}], items)
    assert bool(result["counts"]["quote_verified"]) is verified


@pytest.mark.parametrize("change", [
    {"verdict": "match"}, {"evidence_quote": "x" * 201}, {"rationale": "x" * 121},
    {"evidence_ref": 0}, {"evidence_ref": True}, {"evidence_ref": "1"},
    {"evidence_quote": None}, {"extra": "not allowed"}, {"id": ""},
])
def test_schema_violations(change):
    row = {"id": "supported", "verdict": "supported", "evidence_quote": "LIMIT = 7",
           "evidence_ref": 1, "rationale": ""}
    with pytest.raises(ValueError):
        validate_judgments([{**row, **change}])


def test_duplicates_unknown_ids_and_missing_ref(tmp_path):
    items = load_sent_items(sent_packets(tmp_path))
    row = {"id": "supported", "verdict": "supported", "evidence_quote": "LIMIT = 7", "rationale": ""}
    with pytest.raises(ValueError):
        verify_judgments([row, row], items)
    with pytest.raises(ValueError):
        verify_judgments([{**row, "id": "unseen"}], items)
    result = verify_judgments([row], items)
    assert result["counts"]["not_enough_evidence"] == 1 and not result["items"]


def test_manifest_hash_and_path_defenses(tmp_path):
    folder = sent_packets(tmp_path)
    file = folder / "packet-001.json"
    file.write_text(file.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA"):
        load_sent_items(folder)
    manifest = read_json(folder / "manifest.json")
    manifest["packets"][0]["file"] = "../elsewhere.json"
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="파일명"):
        load_sent_items(folder)


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "judge.json"
    path.write_text('[{"id":"a","id":"b"}]', encoding="utf-8")
    with pytest.raises(ValueError, match="중복"):
        read_json(path)


def test_checker_summary_status_exit_code_remain_unchanged(tmp_path):
    folder = sent_packets(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("alpha.py:1\n", encoding="utf-8")
    checked = run(report)
    snapshot = deepcopy(checked)
    advisory = verify_files(FIXTURE, folder)
    rendered = render_with_advisory(checked, advisory)
    assert checked == snapshot
    assert rendered.index("전체 상태:") < rendered.index("LLM 참고 표시")
    base = [sys.executable, "-B", "-m", "smtw", "claims"]
    before = subprocess.run([*base, "check", "--report", str(report), "--format", "json"],
                            capture_output=True, text=True, encoding="utf-8")
    combined = subprocess.run([*base, "assist", "--verify", str(FIXTURE), "--packets", str(folder),
                               "--report", str(report), "--format", "json"],
                              capture_output=True, text=True, encoding="utf-8")
    data = json.loads(combined.stdout)
    assert combined.returncode == before.returncode == 3
    for field in ("status", "summary", "claims", "unchecked_count", "timed_out"):
        assert data["checker"][field] == json.loads(before.stdout)[field]
    assert data["assist"]["counts"]["contradicted"] == 1


def test_verify_cli_without_report_and_schema_error(tmp_path):
    folder = sent_packets(tmp_path)
    command = [sys.executable, "-B", "-m", "smtw", "claims", "assist",
               "--verify", str(FIXTURE), "--packets", str(folder), "--format", "json"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0 and json.loads(result.stdout)["model_calls"] == 0
    broken = tmp_path / "bad.json"
    broken.write_text('[{"id":"supported","verdict":"match","rationale":""}]', encoding="utf-8")
    command[command.index("--verify") + 1] = str(broken)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 2 and "error" in json.loads(result.stdout)


def test_verification_never_spawns_process_or_network(tmp_path, monkeypatch):
    import socket
    folder = sent_packets(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("verification must only read sent packets")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert verify_files(FIXTURE, folder)["counts"]["quote_verified"] == 2


@pytest.mark.parametrize("value,expected_exit", [(7, 0), (8, 1)])
def test_supported_advisory_cannot_promote_checker_fail_or_change_pass(tmp_path, value, expected_exit):
    folder = sent_packets(tmp_path)
    report = tmp_path / "report.md"
    report.write_text(f"`alpha.py:1` “LIMIT = {value}”\n", encoding="utf-8")
    actual = tmp_path / "alpha.py"
    actual.write_text("LIMIT = 7\n", encoding="utf-8")
    common = ["--report", str(report), "--source-file", f"alpha.py={actual}", "--format", "json"]
    base = [sys.executable, "-B", "-m", "smtw", "claims"]
    before = subprocess.run([*base, "check", *common], capture_output=True, text=True, encoding="utf-8")
    combined = subprocess.run([*base, "assist", "--verify", str(FIXTURE), "--packets", str(folder), *common],
                              capture_output=True, text=True, encoding="utf-8")
    assert before.returncode == combined.returncode == expected_exit, before.stdout + combined.stdout
    a, b = json.loads(before.stdout), json.loads(combined.stdout)["checker"]
    a.pop("elapsed_seconds")
    b.pop("elapsed_seconds")
    assert a == b


def test_claims_cli_utf8_without_environment_overrides(tmp_path):
    """Piped cp949 streams must work even without UTF-8/legacy-console settings."""
    import os
    from tests.claims_judge_support import make_scope
    scope_file = make_scope(tmp_path)
    folder = sent_packets(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("alpha.py:1\n", encoding="utf-8")
    # Explicit cp949 makes this regression meaningful on non-Korean hosts too.
    wrapper = (
        "import sys; sys.stdout.reconfigure(encoding='cp949'); "
        "sys.stderr.reconfigure(encoding='cp949'); "
        "from smtw.cli import main; code=main(); "
        "sys.stderr.write('diagnostic \\u2014 UTF-8\\n'); raise SystemExit(code)"
    )
    for legacy in (None, "1"):
        env = dict(os.environ)
        for key in ("PYTHONUTF8", "PYTHONIOENCODING", "PYTHONLEGACYWINDOWSSTDIO"):
            env.pop(key, None)
        if legacy is not None:
            env["PYTHONLEGACYWINDOWSSTDIO"] = legacy
        cases = [
            (["assist", "--verify", str(FIXTURE), "--packets", str(folder), "--format", "text"], 0),
            (["assist", "--verify", str(FIXTURE), "--packets", str(folder), "--format", "json"], 0),
            (["check", "--report", str(report), "--format", "text"], 3),
            (["assist", "--report", str(report), "--select-only", "--format", "json"], 0),
            (["assist", "--report", str(report), "--dry-run", "--case-id", "example-case",
              "--scope-file", str(scope_file), "--out",
              str(tmp_path / ("dry-" + str(legacy))), "--format", "json"], 0),
        ]
        for args, code in cases:
            result = subprocess.run([sys.executable, "-B", "-c", wrapper, "claims", *args],
                                    capture_output=True, env=env)
            text = result.stdout.decode("utf-8")
            assert result.returncode == code, text + result.stderr.decode("utf-8")
            assert result.stderr.decode("utf-8").splitlines() == ["diagnostic — UTF-8"]
            if args[-1] == "json":
                assert isinstance(json.loads(text), dict)
            elif args[0] == "assist":
                assert "LLM 참고 표시 — 판정 아님" in text
            else:
                assert "전체 상태: INCOMPLETE" in text
