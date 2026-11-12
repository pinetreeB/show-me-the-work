from pathlib import Path

import pytest

from smtw.claims import paths
from smtw.claims.advisory import verify_judgments
from smtw.claims.assist import select_candidates
from smtw.claims.judge_scope import directly_referenced, load_scope, work_source_basis
from smtw.claims.packets import PacketSource, build_packets, hard_excluded
from test_claims_p3_quotes import item, judgment
from test_claims_p3_sources import scope_file


@pytest.mark.parametrize("mention", ["plan.md에서 확인", "plan.md를 확인", "plan.md.", "plan.md는 문서",
                                    "C:/synthetic/tasks/plan.md에서 확인", "C:/synthetic/tasks/plan.md."])
def test_korean_suffix_and_terminal_period_are_direct_mentions(mention):
    assert directly_referenced(mention, "C:/synthetic/tasks/plan.md")


@pytest.mark.parametrize("mention", ["plan.md.backup", "xplan.md", "plan.mdfoo", "other/plan.md"])
def test_filename_substrings_are_still_not_direct_mentions(mention):
    assert not directly_referenced(mention, "C:/synthetic/tasks/plan.md")


@pytest.mark.parametrize("path", ["judge_contract.py", "test_claims_contract.py", "docs/syntax.md",
                                  "docs/taxonomy.md", "legalese.md"])
def test_ordinary_source_filenames_survive_component_policy(path):
    assert not hard_excluded(path)


@pytest.mark.parametrize("path", ["tokens.txt", "token-list.md", "passwords.txt", "pw.md", "private/data.md",
                                  "accounting/data.md", "invoice/data.md", "nda/file.md", "cust-data/file.md",
                                  "tax-2025/report.md", "pre-tax-post/file.md", "legal/file.md"])
def test_sensitive_components_and_names_remain_blocked(path):
    assert hard_excluded(path)


def test_real_symlink_target_is_screened_without_reading_content(tmp_path):
    protected = tmp_path / "accounting"
    protected.mkdir()
    target = protected / "source.md"
    target.write_text("synthetic only", encoding="utf-8")
    link = tmp_path / "ordinary.md"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip("symlink privilege unavailable: " + type(exc).__name__)
    assert hard_excluded(str(link))


def test_short_name_is_checked_after_metadata_resolution(monkeypatch):
    monkeypatch.setattr(paths, "resolved_path", lambda value: "C:/synthetic/customer-data/source.md")
    assert hard_excluded("C:/synthetic/CUSTOM~1/source.md")
    monkeypatch.setattr(paths, "resolved_path", lambda value: "C:/synthetic/ordinary/source.md")
    assert not hard_excluded("C:/synthetic/ORDINA~1/source.md")
    monkeypatch.setattr(paths, "resolved_path", lambda value: value)
    assert hard_excluded("C:/synthetic/UNKNOW~1/source.md")


def test_scope_rejects_symlink_escape_even_without_sensitive_target(tmp_path):
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    target = outside / "card.md"
    target.write_text("synthetic only", encoding="utf-8")
    link = work / "card.md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    scope = load_scope(scope_file(tmp_path, [str(work)], str(tmp_path / "checkpoint.md")))
    assert not work_source_basis("work-example", str(link), scope, report="card.md")


def test_conflict_keeps_both_versions_when_another_sentence_shares_the_row():
    first = "The " + chr(96) + "Alpha" + chr(96) + " in a.py:3 changed."
    second = "Also b.py:7 and c.py:9 are covered."
    report = first + " " + second
    claim = {"report_line": 1, "text": first, "check_type": "K1",
             "verdict": "unverifiable", "reason_code": "observation_conflict"}
    selected = select_candidates(report, {"claims": [claim]}, fixed_commit=True)
    sources = [PacketSource("a.py", {3: f"Alpha = {value}"}, "transcript_read", observation=str(value))
               for value in (1, 2)]
    sources += [PacketSource("b.py", {7: "Alpha b"}, "transcript_read"),
                PacketSource("c.py", {9: "Alpha c"}, "transcript_read")]
    packets, summary = build_packets(selected, sources, report=Path("report.md"))
    conflict = next(i for p in packets for i in p["data_block"] if i["branch"] == "observation_conflict")
    assert {e["text"] for e in conflict["excerpts"]} == {"3: Alpha = 1", "3: Alpha = 2"}
    assert conflict["claim"] == first
    assert "incomplete_conflict" not in summary["skipped_counts"]


def test_exact_quote_wins_over_a_longer_containing_line():
    quote = "LIMIT = 7  # per minute"
    sent = {"example": item("15: " + quote + "\n18: " + quote + " and more")}
    verified = verify_judgments([judgment(quote)], sent)
    assert verified["items"][0]["evidence_quote"] == quote


@pytest.mark.parametrize("line", ["return calculate_value(input_value) " + "x" * 200,
                                  "return calculate_value(input_value) # [REDACTED:password]"])
def test_normalization_cannot_promote_oversized_or_redacted_line(line):
    result = verify_judgments([judgment("calculate_value")], {"example": item("7: " + line)})
    assert not result["items"] and result["counts"]["quote_not_in_excerpt"] == 1
