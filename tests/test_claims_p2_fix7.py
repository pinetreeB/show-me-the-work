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
