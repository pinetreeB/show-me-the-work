
import pytest

from tests.test_claims_p1e_binding import inspect_files
from tests.claims_support import agy_call, view, make_case
from smtw.claims.check import run

FILES = {"alpha.py": "header\npresent quote in the file\nfooter\n",
         "beta.py": "different file content\n",
         "brief.md": "the switch stays off\n"}

@pytest.mark.parametrize("separator", [" ① ", " (1) ", "<br>", "<br/>", " | ", "\u00a0①\u00a0"])
@pytest.mark.parametrize("quote,expected", [("a fabricated quotation", "FAIL"), ("present quote in the file", "PASS")])
def test_unknown_boundary_uses_one_preceding_file_as_whole_file_candidate(tmp_path, separator, quote, expected):
    report = '| `alpha.py:1` 확인' + separator + '“' + quote + '” |'
    result = inspect_files(tmp_path, report, FILES)
    assert result["status"] == expected
    k5 = [c for c in result["claims"] if c["check_type"] == "K5"]
    assert k5 and all(c["verdict"] == ("mismatch" if expected == "FAIL" else "match") for c in k5)


def test_header_candidate_stays_available_across_empty_enumerated_items(tmp_path):
    result = inspect_files(tmp_path, '`alpha.py:1` ① “present quote in the file” ② “a fabricated quotation”', FILES)
    assert result["status"] == "FAIL"
    assert [c["verdict"] for c in result["claims"] if c["check_type"] == "K5"] == ["match", "mismatch"]


@pytest.mark.parametrize("prefix", ["", "`alpha.py:1` 및 `beta.py:1` 확인"])
def test_no_or_multiple_preceding_files_remain_ambiguous(tmp_path, prefix):
    result = inspect_files(tmp_path, prefix + ' ① “a fabricated quotation”', FILES)
    quotes = [c for c in result["claims"] if c["check_type"] == "K5"]
    assert len(quotes) == 1
    assert (quotes[0]["verdict"], quotes[0]["reason_code"]) == ("unverifiable", "binding_ambiguous")


@pytest.mark.parametrize("intro", ["§3", "문서에서", "문서 §제한에서", "§제한에서"])
def test_section_or_generic_document_word_is_not_a_new_source(tmp_path, intro):
    result = inspect_files(tmp_path, f'`alpha.py:1` {intro} “a fabricated quotation”', FILES)
    assert result["status"] == "FAIL"


def test_explicit_other_document_after_boundary_keeps_its_own_evidence(tmp_path):
    result = inspect_files(tmp_path, '`alpha.py:1` ① 지시서(`brief.md` §3)에서 “the switch stays off”', FILES)
    assert result["status"] != "FAIL"
    assert any(c["check_type"] == "K5" and c["verdict"] == "match" for c in result["claims"])


def test_absence_is_checked_against_all_observed_versions(tmp_path):
    path = "C:/synthetic/alpha.py"
    report, transcript = make_case(tmp_path, '`alpha.py:1` ① “a fabricated quotation”', [
        agy_call("view_file", AbsolutePath=path), view(path, "first observed content\n"),
        agy_call("view_file", AbsolutePath=path), view(path, "second observed content\n"),
    ])
    result = run(report, agy=str(transcript))
    assert result["status"] == "FAIL"
    quote = next(c for c in result["claims"] if c["check_type"] == "K5")
    assert quote["source"]["quote_check"]["version_count"] == 2

def test_named_document_without_file_is_an_unknown_source(tmp_path):
    result = inspect_files(tmp_path, '`alpha.py:1` 운영지침에서 “a fabricated quotation”', FILES)
    quotes = [c for c in result["claims"] if c["check_type"] == "K5"]
    assert quotes and all(c["reason_code"] == "binding_ambiguous" for c in quotes)


def test_nearest_preceding_item_supersedes_older_file(tmp_path):
    result = inspect_files(tmp_path, '`beta.py:1` ① `alpha.py:1` ② “present quote in the file”', FILES)
    quotes = [c for c in result["claims"] if c["check_type"] == "K5"]
    assert quotes and all(c["verdict"] == "match" for c in quotes)

def test_ordinary_code_location_is_not_a_document_declaration(tmp_path):
    result = inspect_files(tmp_path, '`alpha.py:1` 근처의 분기에서 `if value > 10:`',
                           {"alpha.py": "header\nif value > 10:\n"})
    assert result["status"] == "PASS"
