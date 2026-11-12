

import pytest

from smtw.claims.advisory import load_sent_items, verify_judgments
from smtw.claims.model import Budget
from smtw.claims.packets import encoded, transcript_sources, write_packets
from tests.claims_support import agy_call, generic, make_case, view, write_transcript
from tests.test_claims_fix8a import inspect, atoms
from tests.test_claims_packets import candidate, packetize, source


@pytest.mark.parametrize("joiner", [" 및 ", " "])
def test_own_exact_quotes_on_long_line_match(tmp_path, joiner):
    text = "prefix " * 20 + "alpha line one " + "middle " * 20 + "beta line two " + "suffix " * 20
    result = inspect(tmp_path, '`long.md:2` “alpha line one”' + joiner + '“beta line two”',
                     "title\n" + text + "\nend\n", "long.md")
    assert result["status"] == "PASS"
    assert len(atoms(result, "K1")) == 2
    assert all(c["verdict"] == "match" for c in atoms(result, "K1"))


@pytest.mark.parametrize("rhs", ["1", "10 + 1", "10 if active else 1"])
def test_qualified_assignment_cannot_match_rhs_prefix(tmp_path, rhs):
    result = inspect(tmp_path, '`sample.py:2` `Base.limit: int = 1`',
                     f"class Base:\n    limit: int = {rhs}\n", "sample.py")
    if rhs == "1":
        assert result["status"] == "INCOMPLETE"
        assert any(c["reason_code"] == "paraphrased_quote" for c in result["claims"])
    else:
        assert result["status"] == "FAIL"


def test_qualified_assignment_rejects_shorter_number(tmp_path):
    result = inspect(tmp_path, '`sample.py:2` `Base.limit: int = 1`',
                     "class Base:\n    limit: int = 10\n", "sample.py")
    assert result["status"] == "FAIL"


@pytest.mark.parametrize("quote,expected", [
    ("if value > 10 and owner(ctx.user.id):", "match"),
    ("if value > 999 and owner(ctx.user.id):", "unverifiable"),
])
def test_unattached_code_quote_preserved_and_compared_in_whole_file(tmp_path, quote, expected):
    content = "title\nif value > 10 and owner(ctx.user.id):\n    pass\nend\n"
    result = inspect(tmp_path, f"`sample.py:1` (C09)와 `sample.py:4` 주변 설명 `{quote}`",
                     content, "sample.py")
    claims = [c for c in atoms(result, "K5") if quote in c["text"]]
    assert len(claims) == 1 and claims[0]["verdict"] == expected
    assert claims[0]["source"]["whole_file_unattached_quote"] is True
    if expected == "unverifiable":
        assert claims[0]["reason_code"] == "binding_ambiguous"


def test_unattached_code_with_ambiguous_path_keeps_unknown_atom(tmp_path):
    result = inspect(tmp_path, "`a.py:1` (C09)와 `b.py:2` 주변 설명 `if value > 999:`")
    claims = atoms(result, "K5")
    assert len(claims) == 1
    assert (claims[0]["verdict"], claims[0]["reason_code"]) == ("unverifiable", "binding_ambiguous")


def test_adjective_small_amount_is_not_reported_claim(tmp_path):
    result = inspect(tmp_path, "코드가 적은 `sample.py`는 총 500줄이지만 괜찮다.", "pass\n", "sample.py")
    assert result["status"] == "FAIL"
    assert all(c["reason_code"] != "reported_claim" for c in atoms(result, "K2"))


@pytest.mark.parametrize("quote", ["a", "alpha", "1234567", "[REDACTED:token]", "alpha [REDACTED:token]"])
def test_short_or_redaction_quotes_cannot_be_verified(quote):
    sent = {"a": {"excerpts": [{"ref": 1, "text": "1: alpha 1234567 [REDACTED:token]"}]}}
    result = verify_judgments([{"id": "a", "verdict": "supported", "rationale": "",
                               "evidence_quote": quote, "evidence_ref": 1}], sent)
    assert result["counts"]["quote_verified"] == 0 and not result["items"]
    assert result["counts"]["not_enough_evidence"] == 1


def test_exact_6000_character_item_boundary():
    packets, _ = packetize([candidate(id="a")], [source()])
    length = len(encoded(packets[0]["data_block"][0]))
    at_limit = candidate(id="a" * (6001 - length))
    packets, summary = packetize([at_limit], [source()])
    assert summary["sent_count"] == 1
    assert len(encoded(packets[0]["data_block"][0])) == 6000
    packets, summary = packetize([{**at_limit, "id": at_limit["id"] + "a"}], [source()])
    assert not packets
    assert summary["skipped_counts"] == {"packet_too_large": 1}


def test_read_after_report_write_is_not_packet_source(tmp_path):
    report, transcript = make_case(tmp_path, "sample.py:1", [])
    path = "C:/synthetic/sample.py"
    write_transcript(transcript, [
        agy_call("view_file", AbsolutePath=path), view(path, "BEFORE = 1\n"),
        agy_call("write_to_file", TargetFile=str(report), CodeContent="sample.py:1"), generic("saved"),
        agy_call("view_file", AbsolutePath=path), view(path, "AFTER = 2\n"),
    ])
    sources = transcript_sources(transcript, "agy", report, report.read_text(), {}, Budget(10))
    assert len(sources) == 1
    assert any("BEFORE" in text for text in sources[0].lines.values())


def test_contradicted_sorted_before_lexically_earlier_supported():
    sent = {key: {"excerpts": [{"ref": 1, "text": "1: ordinary evidence"}]} for key in ["a", "z"]}
    rows = [{"id": key, "verdict": verdict, "evidence_ref": 1,
             "evidence_quote": "ordinary evidence", "rationale": ""}
            for key, verdict in [("a", "supported"), ("z", "contradicted")]]
    assert [r["id"] for r in verify_judgments(rows, sent)["items"]] == ["z", "a"]


def test_same_length_packet_tamper_is_caught_by_hash(tmp_path):
    packets, summary = packetize([candidate()], [source("VALUE = 7")])
    write_packets(tmp_path, packets, summary)
    p = tmp_path / "packet-001.json"
    original = p.read_bytes()
    tampered = original.replace(b"VALUE = 7", b"VALUE = 8")
    assert original != tampered and len(original) == len(tampered)
    p.write_bytes(tampered)
    with pytest.raises(ValueError, match="SHA"):
        load_sent_items(tmp_path)


def test_duplicate_class_name_cannot_explain_qualifier(tmp_path):
    content = "class Base:\n    limit: int = 10\nclass Base:\n    limit: int = 99\n"
    result = inspect(tmp_path, "`sample.py:2` `Base.limit: int = 10`", content, "sample.py")
    assert result["status"] == "FAIL"
    assert not any(c["reason_code"] == "paraphrased_quote" for c in result["claims"])

def test_directly_bound_fake_code_still_fails(tmp_path):
    result = inspect(tmp_path, "`sample.py:1` `if value > 999:`",
                     "if value > 10:\n", "sample.py")
    assert result["status"] == "FAIL"

@pytest.mark.parametrize("snippet", [
    "} </dev/null",
    "refreshData({ cancelRefresh: false })",
    "loadItemById(input.itemId, ctx.member.id)",
    "isActive === false",
    "{ adjustment, ...previewPrice } = result",
])
def test_detached_code_forms_never_disappear(tmp_path, snippet):
    result = inspect(tmp_path, f"`sample.py:1` (TAG)와 `sample.py:3` 주변 설명 `{snippet}`",
                     "first\nsecond\nthird\n", "sample.py")
    claims = [c for c in atoms(result, "K5") if snippet in c["text"]]
    assert len(claims) == 1
    assert (claims[0]["verdict"], claims[0]["reason_code"]) == ("unverifiable", "binding_ambiguous")
