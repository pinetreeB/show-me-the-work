"""Synthetic URL visit and captured page evidence regressions."""
import pytest

from smtw.claims.check import run
from tests.claims_support import agy_call, generic, make_case, view

URL = "https://example.invalid/guide"
OTHER_URL = "https://example.invalid/other"
PAGE = "The documented setting remains enabled by default."
WRONG = "The documented setting remains disabled by default."
CONTENT = "C:/synthetic/brain/.system_generated/steps/7/content.md"


def result_for(tmp_path, rows, quote=PAGE):
    report, transcript = make_case(tmp_path, URL + ' 원문 인용 "' + quote + '"', rows)
    result = run(report, agy=str(transcript))
    claims = {c["check_type"]: c for c in result["claims"]}
    return result, claims


@pytest.mark.parametrize("body", [
    "The full content has been saved to: " + CONTENT + "\nPlease use view_file.",
    "Created At: 2026-01-01\nThe full content of the article at " + URL
    + " has been saved to: " + CONTENT + "\nPlease use view_file.",
])
def test_pointer_confirms_visit_without_claiming_body(tmp_path, body):
    result, claims = result_for(tmp_path, [agy_call("read_url_content", Url=URL), generic(body)])
    assert claims["K8"]["verdict"] == "match"
    assert claims["K8"]["reason_code"] == "visited_url"
    assert claims["K5"]["verdict"] == "unverifiable"
    assert not claims["K5"]["source"]["observations"][0]["complete"]
    assert result["status"] == "INCOMPLETE"


@pytest.mark.parametrize("body,status", [
    ("Error: 404 Not Found", "DONE"),
    ("404 Not Found", "DONE"),
    ("HTTP 403 Forbidden", "DONE"),
    ("HTTP Error 404: Not Found", "DONE"),
    ("TimeoutError: request timed out", "DONE"),
    ("Request timed out", "DONE"),
    ("Blocked: access denied", "DONE"),
    ("Access blocked", "DONE"),
    ('{"isError": true, "message": "unavailable"}', "DONE"),
    ("The full content has been saved to: " + CONTENT, "ERROR"),
])
def test_failed_url_result_never_confirms_visit(tmp_path, body, status):
    _, claims = result_for(tmp_path, [agy_call("read_url_content", Url=URL), generic(body, status)])
    assert claims["K8"]["verdict"] == "unverifiable"
    assert claims["K8"]["reason_code"] == "read_failed"
    assert claims["K5"]["verdict"] == "unverifiable"


@pytest.mark.parametrize("name,args,body", [
    ("search_web", {"query": URL}, URL + "\n" + PAGE),
    ("read_url_content", {"Url": OTHER_URL}, "The linked guide is " + URL + "\n" + PAGE),
    ("browser_snapshot", {}, "A related link is " + URL + "\n" + PAGE),
])
def test_query_and_body_links_do_not_confirm_visit(tmp_path, name, args, body):
    _, claims = result_for(tmp_path, [agy_call(name, **args), generic(body)])
    assert claims["K8"]["verdict"] != "match"
    assert claims["K5"]["verdict"] == "unverifiable"


def test_error_terms_inside_page_are_not_read_failures(tmp_path):
    body = PAGE + "\nThe guide discusses 404, timeout and blocked requests."
    _, claims = result_for(tmp_path, [agy_call("read_url_content", Url=URL), generic(body)])
    assert claims["K8"]["verdict"] == claims["K5"]["verdict"] == "match"


def pointer_rows(path=CONTENT):
    return [agy_call("read_url_content", Url=URL),
            generic("Created At: 2026-01-01\nThe full content of the article at "
                    + URL + " has been saved to: " + path + "\nPlease use view_file.")]


@pytest.mark.parametrize("saved,called,header", [
    (CONTENT, CONTENT.replace("/", "\\"), CONTENT.upper()),
    (CONTENT.replace("/", "\\"), CONTENT, CONTENT.replace("/", "\\")),
    ("file:///" + CONTENT, CONTENT, CONTENT),
    (CONTENT.replace("/", "//"), CONTENT.replace("/", "\\"), CONTENT),
    ("file:////" + CONTENT.replace("/", "//"), CONTENT, CONTENT),
])
@pytest.mark.parametrize("quote,expected", [(PAGE, "match"), (WRONG, "unverifiable")])
def test_partial_path_forms_link_without_inventing_absence(tmp_path, saved, called, header, quote, expected):
    rows = pointer_rows(saved) + [
        agy_call("view_file", AbsolutePath=called, StartLine=2, EndLine=2),
        view(header, "heading\n" + PAGE + "\nfooter\n", start=2, end=2)]
    _, claims = result_for(tmp_path, rows, quote)
    assert claims["K8"]["verdict"] == "match"
    assert claims["K5"]["verdict"] == expected
    assert all(not e["complete"] for e in claims["K5"]["source"]["observations"])


@pytest.mark.parametrize("args", [{"StartLine": 4, "EndLine": 4}, {"ContentOffset": 120}])
@pytest.mark.parametrize("quote,expected", [(PAGE, "match"), (WRONG, "unverifiable")])
def test_unheaded_partial_view_never_becomes_full_page(tmp_path, args, quote, expected):
    rows = pointer_rows() + [agy_call("view_file", AbsolutePath=CONTENT, **args), generic(PAGE)]
    _, claims = result_for(tmp_path, rows, quote)
    assert claims["K5"]["verdict"] == expected
    assert all(not e["complete"] for e in claims["K5"]["source"]["observations"])


@pytest.mark.parametrize("quote,expected", [(PAGE, "match"), (WRONG, "unverifiable")])
def test_separate_partial_ranges_of_one_pointer(tmp_path, quote, expected):
    text = "first section\n" + PAGE + "\nlast section\n"
    rows = pointer_rows() + [
        agy_call("view_file", AbsolutePath=CONTENT.replace("/", "\\"), StartLine=1, EndLine=1),
        view(CONTENT, text, start=1, end=1),
        agy_call("view_file", AbsolutePath=CONTENT, StartLine=2, EndLine=2),
        view(CONTENT, text, start=2, end=2)]
    _, claims = result_for(tmp_path, rows, quote)
    assert claims["K5"]["verdict"] == expected
    assert len(claims["K5"]["source"]["observations"]) == 2


@pytest.mark.parametrize("second,expected", [(PAGE, "match"), (WRONG, "unverifiable")])
def test_every_saved_version_participates(tmp_path, second, expected):
    other = CONTENT.replace("/7/", "/8/")
    rows = pointer_rows() + [agy_call("view_file", AbsolutePath=CONTENT), view(CONTENT, PAGE)]
    rows += pointer_rows(other) + [agy_call("view_file", AbsolutePath=other), view(other, second)]
    _, claims = result_for(tmp_path, rows)
    assert claims["K5"]["verdict"] == expected
    assert len(claims["K5"]["source"]["observations"]) == 2


def test_fabricated_quote_absent_from_all_full_versions_fails(tmp_path):
    other = CONTENT.replace("/7/", "/8/")
    rows = pointer_rows() + [agy_call("view_file", AbsolutePath=CONTENT), view(CONTENT, PAGE)]
    rows += pointer_rows(other) + [agy_call("view_file", AbsolutePath=other), view(other, PAGE)]
    result, claims = result_for(tmp_path, rows, WRONG)
    assert claims["K5"]["verdict"] == "mismatch" and result["status"] == "FAIL"


def test_reused_pointer_is_not_backfilled_from_a_later_version(tmp_path):
    rows = pointer_rows() + pointer_rows() + [
        agy_call("view_file", AbsolutePath=CONTENT), view(CONTENT, PAGE)]
    _, claims = result_for(tmp_path, rows)
    assert claims["K5"]["verdict"] == "unverifiable"


def browser_call(**arguments):
    return agy_call("call_mcp_tool", ServerName="browser", ToolName="browser_evaluate",
                    Arguments=arguments)


def block(text=PAGE, url=URL, close=True, nonce="synthetic"):
    return ("[UNTRUSTED_PAGE_CONTENT nonce=synthetic origin=" + url + "]\n" + text
            + ("\n[/UNTRUSTED_PAGE_CONTENT nonce=" + nonce + "]" if close else ""))


@pytest.mark.parametrize("close,quote,expected", [
    (True, PAGE, "match"), (False, PAGE, "match"),
    (True, WRONG, "mismatch"), (False, WRONG, "unverifiable"),
])
def test_browser_origin_block_has_its_own_completeness(tmp_path, close, quote, expected):
    result, claims = result_for(tmp_path, [browser_call(), generic(block(close=close))], quote)
    assert claims["K8"]["verdict"] == "match"
    assert claims["K5"]["verdict"] == expected
    assert claims["K5"]["source"]["observations"][0]["complete"] == close
    if expected == "mismatch":
        assert result["status"] == "FAIL"


def test_browser_origin_keeps_other_urls_and_outside_text_out(tmp_path):
    rows = [browser_call(), generic(block(WRONG) + "\n" + PAGE + "\n" + block(PAGE, OTHER_URL))]
    result, claims = result_for(tmp_path, rows)
    assert claims["K5"]["verdict"] == "mismatch" and result["status"] == "FAIL"


def test_wrong_closing_nonce_does_not_establish_full_page(tmp_path):
    _, claims = result_for(tmp_path, [browser_call(), generic(block(nonce="other"))], WRONG)
    assert claims["K5"]["verdict"] == "unverifiable"


@pytest.mark.parametrize("captured,expected", [(False, "unverifiable"), (True, "match")])
def test_browser_tool_output_pointer_uses_captured_view(tmp_path, captured, expected):
    path = "C:/synthetic/tool-output/evaluate-demo.txt"
    rows = [browser_call(url=URL), generic("Page URL: " + URL + "\nFile Path: " + path)]
    if captured:
        rows += [agy_call("view_file", AbsolutePath=path.replace("/", "\\")),
                 view(path, block())]
    _, claims = result_for(tmp_path, rows)
    assert claims["K8"]["verdict"] == "match"
    assert claims["K5"]["verdict"] == expected


def test_browser_pointer_without_url_links_captured_origin(tmp_path):
    path = "C:/synthetic/tool-output/evaluate-demo.txt"
    rows = [browser_call(), generic("File Path: " + path),
            agy_call("view_file", AbsolutePath=path.replace("/", "\\")),
            view(path, block())]
    _, claims = result_for(tmp_path, rows)
    assert claims["K8"]["verdict"] == claims["K5"]["verdict"] == "match"


@pytest.mark.parametrize("rows", [
    [agy_call("view_file", AbsolutePath=CONTENT), view(CONTENT, block())],
    [agy_call("search_web", query=URL), generic(block())],
    [browser_call(), generic(block(), "ERROR")],
    [browser_call(url=OTHER_URL), generic(block())],
])
def test_origin_block_requires_successful_bound_browser_result(tmp_path, rows):
    _, claims = result_for(tmp_path, rows)
    assert claims["K8"]["verdict"] != "match"
    assert claims["K5"]["verdict"] == "unverifiable"


def test_linked_web_body_keeps_inline_backtick_normalization(tmp_path):
    rows = pointer_rows() + [agy_call("view_file", AbsolutePath=CONTENT),
                            view(CONTENT, "The default is `enabled`.")]
    _, claims = result_for(tmp_path, rows, "The default is enabled.")
    assert claims["K5"]["verdict"] == "match"


def test_offset_cannot_turn_chunk_total_into_page_total(tmp_path):
    rows = pointer_rows() + [
        agy_call("view_file", AbsolutePath=CONTENT, ContentOffset=120),
        view(CONTENT, PAGE)]
    _, claims = result_for(tmp_path, rows, WRONG)
    assert claims["K5"]["verdict"] == "unverifiable"


@pytest.mark.parametrize("mode", ["before", "other", "header_mismatch", "failed", "after_report"])
def test_pointer_requires_later_successful_bound_capture(tmp_path, mode):
    other = CONTENT.replace("/7/", "/8/")
    call_path = other if mode == "other" else CONTENT
    header_path = other if mode == "header_mismatch" else call_path
    capture = [agy_call("view_file", AbsolutePath=call_path),
               generic("Error: unavailable") if mode == "failed" else view(header_path, PAGE)]
    rows = (capture + pointer_rows()) if mode == "before" else (pointer_rows() + capture)
    if mode == "after_report":
        report_text = URL + ' 원문 인용 "' + PAGE + '"'
        rows = pointer_rows() + [
            agy_call("write_to_file", TargetFile=str(tmp_path / "report.md"), CodeContent=report_text),
            generic("done")] + capture
    _, claims = result_for(tmp_path, rows)
    assert claims["K8"]["verdict"] == "match"
    assert claims["K5"]["verdict"] == "unverifiable"


@pytest.mark.parametrize("url", ["https://example.invalid/guide", '"https://example.invalid/guide"'])
def test_literal_mcp_arguments_and_alternate_block_end(tmp_path, url):
    from smtw.claims.paths import normalize_path
    assert normalize_path("file:///C:%5Csynthetic%5Cpage.txt") == "c:/synthetic/page.txt"
    call = agy_call("call_mcp_tool", server_name="browseros", tool_name="browser_evaluate",
                    arguments='{"url":"https://example.invalid/guide"}')
    body = '[UNTRUSTED_PAGE_CONTENT nonce="synthetic" origin=' + url + ']\n' + PAGE
    body += '\n[END_UNTRUSTED_PAGE_CONTENT nonce="synthetic"]'
    _, claims = result_for(tmp_path, [call, generic(body)])
    assert claims["K5"]["verdict"] == claims["K8"]["verdict"] == "match"


def test_complete_capture_still_rejects_a_wrong_word_or_number(tmp_path):
    rows = pointer_rows() + [agy_call("view_file", AbsolutePath=CONTENT),
                            view(CONTENT, "The documented count is `7` entries.")]
    result, claims = result_for(tmp_path, rows, "The documented count is 9 entries.")
    assert result["status"] == "FAIL" and claims["K5"]["verdict"] == "mismatch"
