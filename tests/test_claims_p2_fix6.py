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
