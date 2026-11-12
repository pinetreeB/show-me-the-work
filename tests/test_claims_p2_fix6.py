import pytest

from smtw.claims.check import run
from tests.claims_support import agy_call, generic, make_case, view

URL = "https://example.invalid/guide"
PAGE = "The documented setting remains enabled by default."
WRONG = "The documented setting remains disabled by default."
CONTENT = "C:/synthetic/brain/.system_generated/steps/7/content.md"


@pytest.mark.parametrize("saved", ["has been saved to", "was saved to"])
@pytest.mark.parametrize("mode,quote,expected", [
    ("full", PAGE, "match"), ("pointer", PAGE, "unverifiable"),
    ("full", WRONG, "mismatch"), ("partial", WRONG, "unverifiable"),
    ("partial", PAGE, "match"), ("other", PAGE, "unverifiable"),
])
def test_url_pointer_uses_only_captured_page_reads(tmp_path, saved, mode, quote, expected):
    report = URL + ' 원문 인용 "' + quote + '"'
    notice = f"The full content of the page {saved}: {CONTENT}\nPlease use view_file."
    rows = [agy_call("read_url_content", Url=URL), generic(notice)]
    if mode != "pointer":
        path = CONTENT.replace("/7/", "/8/") if mode == "other" else CONTENT
        rows += [agy_call("view_file", AbsolutePath=path),
                 view(path, PAGE + "\nAnother page sentence.\n", end=1 if mode == "partial" else None)]
    r, t = make_case(tmp_path, report, rows)
    result = run(r, agy=str(t))
    claim, = [c for c in result["claims"] if c["check_type"] == "K5"]
    assert claim["verdict"] == expected
    visit, = [c for c in result["claims"] if c["check_type"] == "K8"]
    assert visit["verdict"] == "match"
    if mode in {"pointer", "other", "partial"}:
        assert not any(e["complete"] for e in claim["source"]["observations"])
    if expected == "mismatch":
        assert result["status"] == "FAIL"


@pytest.mark.parametrize("suffix", [".bak", "/archive"])
def test_other_saved_file_is_not_a_page_content_pointer(tmp_path, suffix):
    report = URL + ' 원문 인용 "' + PAGE + '"'
    r, t = make_case(tmp_path, report, [
        agy_call("read_url_content", Url=URL),
        generic(PAGE + " Storage was saved to: " + CONTENT + suffix)])
    claim, = [c for c in run(r, agy=str(t))["claims"] if c["check_type"] == "K5"]
    assert claim["verdict"] == "match"


@pytest.mark.parametrize("marker", ["주장", "라고 함", "이라 적음", "claimed", "vs"])
def test_attributed_line_count_is_retained_unverifiable(tmp_path, marker):
    report = f"¤sample.md¤: 53줄 {marker} 31~32".replace("¤", chr(96))
    r, t = make_case(tmp_path, report, [
        agy_call("view_file", AbsolutePath="C:/synthetic/sample.md"),
        view("C:/synthetic/sample.md", "one\ntwo\nthree\n")])
    claim, = [c for c in run(r, agy=str(t))["claims"] if c["check_type"] == "K2"]
    assert claim["verdict"] == "unverifiable"
    assert claim["reason_code"] == "reported_claim"


def test_count_claim_inside_measured_quote_is_attributed(tmp_path):
    report = "(체크포인트 ¤checkpoint.md:45¤ 실측: ¤'observed-state'¤, ¤config 53줄 주장 vs 31~32¤)"
    text = "before\n" * 44 + "padding " * 70 + "'observed-state'\n"
    r, t = make_case(tmp_path, report.replace("¤", chr(96)), [
        agy_call("view_file", AbsolutePath="C:/synthetic/checkpoint.md"),
        view("C:/synthetic/checkpoint.md", text)])
    result = run(r, agy=str(t))
    claim, = [c for c in result["claims"] if c["check_type"] == "K2"]
    assert claim["verdict"] == "unverifiable"
    assert claim["reason_code"] == "reported_claim"
    assert result["status"] != "FAIL"


def test_own_false_line_count_still_fails(tmp_path):
    report = "config는 53줄이다(¤config.json¤)".replace("¤", chr(96))
    r, t = make_case(tmp_path, report, [
        agy_call("view_file", AbsolutePath="C:/synthetic/config.json"),
        view("C:/synthetic/config.json", '{\n  "enabled": true\n}\n')])
    result = run(r, agy=str(t))
    claim, = [c for c in result["claims"] if c["check_type"] == "K2"]
    assert claim["verdict"] == "mismatch" and result["status"] == "FAIL"
