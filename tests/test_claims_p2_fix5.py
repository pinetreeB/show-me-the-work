import pytest

from smtw.claims.check import run
from tests.claims_support import agy_call, generic, make_case, view


@pytest.mark.parametrize("accurate", [True, False])
@pytest.mark.parametrize("prefix", ["", "출력 근거: "])
def test_measured_parenthetical_literal_survives_two_long_reads(tmp_path, accurate, prefix):
    quote_a = "'available-state'"
    quote_b = "'different-state'"
    reported = [quote_a, quote_b] if accurate else ["'invented-state'", "'fabricated-state'"]
    report = (prefix + "근거: ¤trace.jsonl:20¤…¤trace.jsonl:22¤…"
              f"(체크포인트 ¤checkpoint.md:45¤ 실측: ¤{reported[0]}¤, ¤{reported[1]}¤)"
              + '\n후속 설명은 "an unrelated prose quotation" 입니다.')
    report = report.replace("¤", chr(96))
    rows = [agy_call("view_file", AbsolutePath="C:/synthetic/trace.jsonl"),
            view("C:/synthetic/trace.jsonl", "entry\n" * 30, start=1, end=2)]
    for first in ["before", "changed"]:
        text = first + "\n" + "before\n" * 43 + "padding " * 70 + quote_a + " tail " * 20 + "\n" + quote_b
        rows += [agy_call("view_file", AbsolutePath="C:/synthetic/checkpoint.md"),
                 view("C:/synthetic/checkpoint.md", text)]
    r, t = make_case(tmp_path, report, rows)
    result = run(r, agy=str(t))
    claims = [c for c in result["claims"]
              if c["check_type"] == "K1" and "checkpoint.md:45" in c["text"]]
    assert len(claims) == 2
    if accurate:
        assert any(c["verdict"] == "match" and reported[0] in c["text"] for c in claims)
        assert all(c["verdict"] != "mismatch" for c in claims)
    else:
        assert result["status"] == "FAIL"
        assert any(c["verdict"] == "mismatch" for c in claims)


def test_parenthetical_reference_without_quote_stays_range_only(tmp_path):
    report = "근거: ¤checkpoint.md:45¤".replace("¤", chr(96))
    rows = []
    for first in ["before", "changed"]:
        rows += [agy_call("view_file", AbsolutePath="C:/synthetic/checkpoint.md"),
                 view("C:/synthetic/checkpoint.md", first + "\n" + "padding\n" * 44)]
    r, t = make_case(tmp_path, report, rows)
    claim, = [c for c in run(r, agy=str(t))["claims"] if c["check_type"] == "K1"]
    assert claim["verdict"] == "range_only"
