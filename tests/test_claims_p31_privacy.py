"""Privacy gates use synthetic names only; no real blocklist is accessed."""
import json
from pathlib import Path

import pytest

from smtw.claims import judge
from smtw.claims.packets import build_packets, write_packets
from smtw.claims.privacy import load_blocklist, business_document
from tests.claims_judge_support import make_scope
from test_claims_assist_scope import cli
from test_claims_judge import fake_judge
from test_claims_packets import candidate, source
from test_claims_p3_sources import scope_file

VARIANTS = ["Synthetic Orchid Trading", "sYnThEtIc oRcHiD tRaDiNg",
            "Synthetic \n Orchid\tTrading", "Ｓｙｎｔｈｅｔｉｃ　Ｏｒｃｈｉｄ　Ｔｒａｄｉｎｇ",
            "합성거래처알파", "합 성 거 래 처 알 파"]


@pytest.fixture
def block_file(tmp_path):
    path = tmp_path / "synthetic-list.txt"
    path.write_text("Synthetic Orchid Trading\n합성거래처알파\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("value", VARIANTS)
@pytest.mark.parametrize("field", ["claim", "before", "after", "excerpt"])
def test_blocklist_drops_whole_item_and_never_records_name(tmp_path, block_file, value, field):
    row = candidate()
    evidence = source()
    if field == "excerpt":
        evidence = source("VALUE = 7\n" + value)
    else:
        row[field] = value
    packets, summary = build_packets({"items": [row], "selected_count": 1, "deferred_count": 0},
                                    [evidence], report=tmp_path / "report.md",
                                    blocklist=load_blocklist(block_file))
    assert packets == [] and summary["skipped_counts"] == {"skipped_blocklist": 1}
    assert summary["blocklist"]["count"] == 2
    assert len(summary["blocklist"]["sha256_prefix"]) == 8
    recorded = json.dumps(summary, ensure_ascii=False)
    assert "Orchid" not in recorded and "알파" not in recorded


def packet(claim="The limit is seven.", ident="q-001"):
    return {"version": 1, "instructions": "untrusted data", "data_block": [{
        "id": ident, "claim": claim, "before": "", "after": "", "redactions": {},
        "excerpts": [{"path": "~/example-repo/src/example.py", "ref": 1,
                      "text": "1: MAX_LIMIT = 7", "source": "transcript_read"}]}]}


def options(tmp_path, block_file, executor):
    return dict(scope_file=make_scope(tmp_path), output_root=tmp_path / "judge-output",
                executor=executor, tty=lambda: True, parent_env={}, executable="claude-p",
                usage_reader=lambda: {"known": True, "five_hour": 1, "weekly": 2},
                blocklist_file=block_file)


@pytest.mark.parametrize("value", VARIANTS)
def test_judge_scans_all_packets_before_first_call(tmp_path, block_file, value, capsys):
    folder = tmp_path / "packets"
    policy = load_blocklist(block_file)
    write_packets(folder, [packet(), packet(value, "q-002")],
                  {"case_id": "example-case", "blocklist": policy.metadata()})
    calls = []
    with pytest.raises(ValueError, match="privacy gate") as failure:
        judge.judge(folder, **options(tmp_path, block_file, lambda *args: calls.append(args)))
    assert calls == [] and not (tmp_path / "judge-output").exists()
    assert value not in str(failure.value) + capsys.readouterr().out
    assert "Orchid" not in repr(policy) and policy.sha256 not in repr(policy)


def test_success_logs_only_blocklist_count_and_short_sha(tmp_path, block_file):
    folder = tmp_path / "packets"
    policy = load_blocklist(block_file)
    manifest = write_packets(folder, [packet()], {"case_id": "example-case", "blocklist": policy.metadata()})
    result = judge.judge(folder, **options(tmp_path, block_file, fake_judge))
    assert result["execution"] == "injected"
    log = (tmp_path / "judge-output/calls.jsonl").read_text(encoding="utf-8")
    record = json.loads(log)
    assert record["blocklist"] == manifest["blocklist"] == policy.metadata()
    assert "Orchid" not in log and "알파" not in log and policy.sha256 not in log


def test_list_change_before_a_later_call_stops_execution(tmp_path, block_file):
    folder = tmp_path / "packets"
    policy = load_blocklist(block_file)
    write_packets(folder, [packet(), packet(ident="q-002")],
                  {"case_id": "example-case", "blocklist": policy.metadata()})
    calls = []
    def fake(*args):
        calls.append(1)
        block_file.write_text("Another Synthetic Entry\n", encoding="utf-8")
        return fake_judge(*args)
    with pytest.raises(ValueError, match="blocklist changed"):
        judge.judge(folder, **options(tmp_path, block_file, fake))
    assert calls == [1]


@pytest.mark.parametrize("provided", [False, True])
def test_work_scope_requires_existing_list_before_report_read(tmp_path, provided):
    scope = scope_file(tmp_path)
    args = ["--dry-run", "--case-id", "work-example", "--scope-file", scope,
            "--report", tmp_path / "nonexistent-report.md", "--out", tmp_path / "packets"]
    if provided:
        args += ["--blocklist-file", tmp_path / "missing-list.txt"]
    result = cli(*args)
    assert result.returncode == 2
    error = json.loads(result.stdout)["error"]
    assert "blocklist" in error and "nonexistent-report" not in error


def test_judge_work_scope_without_list_refuses(tmp_path):
    scope = scope_file(tmp_path)
    folder = tmp_path / "packets"
    row = packet()
    row["data_block"][0]["excerpts"][0].update(
        path="C:/synthetic/tasks/example/card.md", scope_basis="report_reference")
    write_packets(folder, [row], {"case_id": "work-example"})
    with pytest.raises(ValueError, match="blocklist-file"):
        judge.judge(folder, scope_file=scope, output_root=tmp_path / "judge-output", tty=lambda: True,
                    parent_env={}, executor=lambda *args: pytest.fail("unexpected execution"),
                    executable="claude-p")


@pytest.mark.parametrize("path", [
    "C:/Users/Synthetic/OneDrive/Offer.docx", "C:/Users/Synthetic/Documents/Offer.xlsx",
    "C:/Users/Synthetic/OneDrive/문서�/견적�.pdf", "Documents/합성�.hwp", "see Documents/Synthetic Offer.pdf",
])
@pytest.mark.parametrize("field", ["claim", "excerpt", "packet_instructions"])
def test_business_document_paths_are_always_blocked(tmp_path, path, field):
    assert business_document(path)
    if field == "packet_instructions":
        row = packet()
        row["instructions"] = path
        folder = tmp_path / "packets"
        write_packets(folder, [row], {"case_id": "example-case"})
        with pytest.raises(ValueError, match="privacy gate"):
            judge.judge(folder, **options(tmp_path, None, lambda *args: pytest.fail("unexpected execution")))
    else:
        row = candidate(**{"claim": path}) if field == "claim" else candidate()
        evidence = source(path) if field == "excerpt" else source()
        packets, summary = build_packets({"items": [row], "selected_count": 1, "deferred_count": 0},
                                        [evidence], report=tmp_path / "report.md")
        assert not packets and summary["skipped_counts"] == {"skipped_business_doc": 1}


def test_empty_list_is_not_an_override(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text(" \n\t", encoding="utf-8")
    with pytest.raises(ValueError, match="at least one"):
        load_blocklist(path, required=True)


@pytest.mark.parametrize("flag", ["--ignore-blocklist", "--allow-blocklist", "--force"])
def test_no_human_override_flags_exist(flag):
    with pytest.raises(SystemExit) as failure:
        judge.main(["--packets", "synthetic", "--scope-file", "synthetic", "--out-root", "synthetic",
                    "--config-dir", "synthetic", "--usage-service", "claude", flag])
    assert failure.value.code == 2


def test_unicode_escaped_packet_values_are_scanned(tmp_path, block_file):
    folder = tmp_path / "packets"
    policy = load_blocklist(block_file)
    manifest = write_packets(folder, [packet("합성거래처알파")],
                             {"case_id": "example-case", "blocklist": policy.metadata()})
    path = folder / manifest["packets"][0]["file"]
    text = json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=True)
    path.write_text(text, encoding="utf-8")
    entry = manifest["packets"][0]
    entry.update(sha256=judge.sha(text.encode("utf-8")), bytes=len(text.encode("utf-8")), characters=len(text))
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="privacy gate"):
        judge.judge(folder, **options(tmp_path, block_file, lambda *args: pytest.fail("unexpected execution")))


def test_usage_override_cannot_bypass_blocklist(tmp_path, block_file):
    folder = tmp_path / "packets"
    policy = load_blocklist(block_file)
    write_packets(folder, [packet("Synthetic Orchid Trading")],
                  {"case_id": "example-case", "blocklist": policy.metadata()})
    with pytest.raises(ValueError, match="privacy gate"):
        judge.judge(folder, allow_unknown_usage=True,
                    **options(tmp_path, block_file, lambda *args: pytest.fail("unexpected execution")))
