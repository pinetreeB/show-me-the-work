import pytest
from smtw.claims.check import run
from tests.claims_support import agy_call, generic, make_case, view

URL = "https://example.invalid/guide#usage"
REAL = "The documented option remains enabled by default."
FAKE = "The documented option remains disabled by default."

@pytest.mark.parametrize("wrapper", ['"{}"', '`"{}"`', '``"{}"``'])
@pytest.mark.parametrize("observed,wrong,verdict", [(True,False,"match"),(True,True,"mismatch"),(False,True,"unverifiable"),(False,False,"unverifiable")])
def test_web_quote_never_inherits_local_file(tmp_path, wrapper, observed, wrong, verdict):
    quote = FAKE if wrong else REAL
    report = '> local `state.json` summary\n> ' + URL + ' 원문 인용 ' + wrapper.format(quote)
    rows = [agy_call("view_file", AbsolutePath="C:/sample/state.json"),view("C:/sample/state.json",'{"enabled":true}')]
    if observed:
        rows += [agy_call("read_url_content",Url=URL),generic(REAL)]
    r,t = make_case(tmp_path,report,rows)
    result = run(r,agy=str(t))
    quotes = [c for c in result["claims"] if c["check_type"]=="K5"]
    assert len(quotes)==1
    assert quotes[0]["verdict"]==verdict

def test_block_formatting_is_not_quote(tmp_path):
    r,t = make_case(tmp_path, '> `state.json` 상태를 요약합니다.\n> 이 줄은 작성자의 설명입니다.', [])
    assert not [c for c in run(r,agy=str(t))["claims"] if c["check_type"]=="K5"]

def test_explicit_local_wrong_quote_still_fails(tmp_path):
    r,t=make_case(tmp_path,'> `state.json` 원문 "invented string never observed"',[
        agy_call("view_file",AbsolutePath="C:/sample/state.json"),view("C:/sample/state.json",'original string actually observed')])
    result=run(r,agy=str(t))
    assert result["status"]=="FAIL"
    assert any(c["check_type"]=="K5" and c["verdict"]=="mismatch" for c in result["claims"])

def test_web_anchor_without_read_stays_unverifiable(tmp_path):
    r,t=make_case(tmp_path,'> `state.json` summary\n> #usage 원문 인용 "The documented option remains enabled by default."',[
        agy_call("view_file",AbsolutePath="C:/sample/state.json"),view("C:/sample/state.json",'original string actually observed')])
    claims=[c for c in run(r,agy=str(t))["claims"] if c["check_type"]=="K5"]
    assert len(claims)==1 and claims[0]["verdict"]=="unverifiable"

from smtw.claims.packets import redact

@pytest.mark.parametrize("text", [
    "commit auth " + "a1"*20, "| token | " + "a1"*20 + " |",
    "admin_password=12345678", "account_token=987654321012",
    "password=test-1234", "password=secret.hunter2", "password=MyPassword",
    "SHA256 verified then key " + "a1"*32, "config.json: " + "a1"*32,
    "token: int; actual synthetic_value_4821", "token=None # actual synthetic_value_4821",
])
def test_exemption_secret_pairs_are_redacted(text):
    out, counts = redact(text)
    assert counts and out != text
    assert text.rsplit(" ",1)[-1] not in out or text.endswith(" |")

@pytest.mark.parametrize("text", [
    "commit " + "a1"*20, "SHA256: " + "a1"*32,
    "max_tokens: 4096", "ownerToken: string",
    "login(user, password=password)", "api_key=apiKey",
])
def test_exemption_benign_pairs_preserved(text):
    assert redact(text)[0] == text

@pytest.mark.parametrize("prefix", ["제안", "권고", "변경 후", "예:", "수정 예", "→"])
def test_proposal_quotes_retained_unverifiable(tmp_path,prefix):
    r,t=make_case(tmp_path,'`sample.py:1` ① '+prefix+' `if value > 42:`',[
        agy_call("view_file",AbsolutePath="C:/sample/sample.py"),view("C:/sample/sample.py","if value > 10:")])
    result=run(r,agy=str(t))
    assert result["status"]!="FAIL"
    assert any(c["check_type"]=="K5" and c["reason_code"]=="non_file_quote" for c in result["claims"])


def test_plain_code_string_note_does_not_attach_previous_line_reference(tmp_path):
    r,t=make_case(tmp_path,'`file.py:1` reference\nThe next UI message is `"a different code string"` in the example.',[
        agy_call("view_file",AbsolutePath="C:/sample/file.py"),
        view("C:/sample/file.py","original code string")])
    result=run(r,agy=str(t))
    assert result["status"]!="FAIL"
    assert next(c for c in result["claims"] if c["check_type"]=="K1")["verdict"]=="range_only"


def test_recognized_optional_type_but_not_arbitrary_value_is_exempt():
    assert redact("token: Optional[str]")==("token: Optional[str]",{})
    assert redact("token: Optional[secret_hunter2]")[1]
