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
