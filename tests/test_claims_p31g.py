"""Synthetic output attribution, privacy variants, and frozen-code gates."""
import json
from pathlib import Path
import unicodedata

import pytest

from smtw.claims.model import Budget
from smtw.claims.packets import transcript_sources
from smtw.claims.privacy import load_blocklist
from smtw.claims.judge import child_environment
from tests.claims_support import agy_call, generic, make_case

ROOT = "C:/synthetic/tasks/example"
BODY = "IdentAaa returns seven ms. APPROVED_MARKER"


def packet_sources(tmp_path, rows, kind="codex"):
    report, transcript = make_case(tmp_path, "IdentAaa returns 7 ms. card.md", rows)
    return transcript_sources(transcript, kind, report, report.read_text(encoding="utf-8"), {}, Budget(15))


def code_rows(code, outputs):
    return [
        {"type":"response_item","payload":{"type":"custom_tool_call","name":"exec","call_id":"code1","input":code}},
        {"type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"code1","output":outputs}},
    ]


def literal(path="card.md"):
    return 'await tools.exec_command({cmd: "cat '+ROOT+'/'+path+'"});'


def output(body=BODY, index=None):
    result={"output":body,"exit_code":0}
    return result if index is None else {"i":index,"value":result}


@pytest.mark.parametrize("extra", [
    'await tools.exec_command({cmd: variable});',
    'await tools.exec_command({cmd: '+chr(96)+'cat dynamic'+chr(96)+'});',
    "await tools.exec_command({cmd: 'cat "+ROOT+"/.env'});",
    'await tools.read_file({path: "'+ROOT+'/.env"});',
])
@pytest.mark.parametrize("outputs", [[output(),output("EXCLUDED_MARKER")], output("EXCLUDED_MARKER",0)])
def test_codemode_unparsed_call_invalidates_entire_group(tmp_path, extra, outputs):
    assert not packet_sources(tmp_path,code_rows(literal()+extra,outputs))


@pytest.mark.parametrize("outputs", [
    [output(),output("EXCLUDED_MARKER")],
    [output(index=0),output("EXCLUDED_MARKER",0)],
    output("EXCLUDED_MARKER",-1), output("EXCLUDED_MARKER",True),
    output("EXCLUDED_MARKER",7),
])
def test_codemode_extra_or_invalid_indices_are_rejected(tmp_path,outputs):
    assert not packet_sources(tmp_path,code_rows(literal(),outputs))


def test_codemode_single_literal_is_not_packet_evidence(tmp_path):
    # p3-1h: code-mode output is never packet evidence, even when unambiguous.
    assert not packet_sources(tmp_path,code_rows(literal(),output(index=0)))


@pytest.mark.parametrize("outputs", [
    [output(index=0),output("EXCLUDED_MARKER",0)],
    [output(),output("EXCLUDED_MARKER",1)],
    [output("EXCLUDED_MARKER")],
])
def test_codemode_ambiguous_multi_call_mapping_is_rejected(tmp_path,outputs):
    assert not packet_sources(tmp_path,code_rows(literal("first.md")+literal(),outputs))


def test_duplicate_output_rows_do_not_reuse_a_call(tmp_path):
    rows=code_rows(literal(),output())
    rows.append({"type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"code1","output":output("EXCLUDED_MARKER")}})
    assert not packet_sources(tmp_path,rows)


@pytest.mark.parametrize("outputs", [
    [output(index=1),output("second",0)],
    [output("second"),output()],
    [output(index=1)],
])
def test_codemode_fully_parsed_unique_mapping_is_not_packet_evidence(tmp_path,outputs):
    assert not packet_sources(tmp_path,code_rows(literal("first.md")+literal("card.md"),outputs))


@pytest.mark.parametrize("interruption", [None,"ERROR_MESSAGE","SYSTEM_MESSAGE"])
def test_agy_missing_result_or_interruption_rejects_shell_batch(tmp_path,interruption):
    planner={"type":"PLANNER_RESPONSE","tool_calls":[
        {"name":"run_command","args":{"CommandLine":"cat "+ROOT+"/card.md"}},
        {"name":"run_command","args":{"CommandLine":"cat "+ROOT+"/.env"}},
    ]}
    rows=[planner]
    if interruption:rows.append({"type":interruption,"content":"cancelled"})
    rows.append(generic("EXCLUDED_MARKER"))
    assert not packet_sources(tmp_path,rows,"agy")


def test_agy_extra_generic_invalidates_prior_shell_output(tmp_path):
    rows=[agy_call("run_command",CommandLine="cat "+ROOT+"/card.md"),generic(BODY),generic("EXCLUDED_MARKER")]
    assert not packet_sources(tmp_path,rows,"agy")


@pytest.mark.parametrize("notice", ["Output has been saved to: steps/3/output.txt",
                                    "Task logs are available at: tasks/task-9.log"])
def test_shell_aux_pointer_never_opens_another_step(tmp_path,notice,monkeypatch):
    aux=tmp_path/"aux"
    target=aux/("steps/3/output.txt" if "steps/" in notice else "tasks/task-9.log")
    target.parent.mkdir(parents=True);target.write_text("EXCLUDED_MARKER",encoding="utf-8")
    original=Path.open
    def guarded(path,*args,**kwargs):
        assert path != target, "shell pointer file opened"
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"open",guarded)
    rows=[agy_call("run_command",CommandLine="cat "+ROOT+"/card.md"),generic(BODY+"\n"+notice)]
    assert not packet_sources(tmp_path,rows,"agy")


@pytest.mark.parametrize("variant", [
    "홍길동상사","ㅎㅗㅇㄱㅣㄹㄷㅗㅇㅅㅏㅇㅅㅏ",
    "홍-길_동.상·사",unicodedata.normalize("NFD","홍길동상사"),
    "Hong-Gildong_Trading", "Ｈｏｎｇ　Ｇｉｌｄｏｎｇ　Ｔｒａｄｉｎｇ",
])
def test_blocklist_normalizes_synthetic_name_variants(tmp_path,variant):
    path=tmp_path/"block.txt";path.write_text("# synthetic names\n홍길동상사 # comment\nHong Gildong Trading\n",encoding="utf-8")
    block=load_blocklist(path)
    assert block.metadata()["count"]==2 and block.matches(variant)


def test_halfwidth_jamo_name_matches(tmp_path):
    source="ㅎㅗㅇㄱㅣㄹㄷㅗㅇㅅㅏㅇㅅㅏ"
    replacements={unicodedata.normalize("NFKC",chr(n)):chr(n) for n in range(0xFFA0,0xFFDD)}
    half="".join(replacements.get(unicodedata.normalize("NFKC",char),char) for char in source)
    path=tmp_path/"block.txt";path.write_text("홍길동상사",encoding="utf-8")
    assert load_blocklist(path).matches(half)


def test_short_name_warns_without_echo_and_remains_blocked(tmp_path):
    path=tmp_path/"block.txt";path.write_text("# ignored\nQ # short\n",encoding="utf-8")
    with pytest.warns(UserWarning,match="shorter than two") as records:
        block=load_blocklist(path)
    assert "Q" not in str(records[0].message)
    assert block.matches("Q") and block.metadata()["count"]==1


@pytest.mark.parametrize("key", ["CLAUDE_CODE_USE_FOUNDRY","claude_code_use_future","CLAUDE_CODE_USE_UNKNOWN"])
def test_all_provider_route_names_refused_without_value_reads(key):
    class NamesOnly(dict):
        def __getitem__(self,key):
            raise AssertionError("environment value read")
    with pytest.raises(ValueError,match="provider override"):
        child_environment(NamesOnly({key:None}))


def test_public_required_flag_prevents_skip(monkeypatch):
    from tests.test_claims_p31f_frozen import test_claims_public_75512be_frozen
    monkeypatch.delenv("SMTW_CLAIMS_PUBLIC_RESULTS",raising=False)
    monkeypatch.setenv("SMTW_REQUIRE_PUBLIC","1")
    with pytest.raises(pytest.fail.Exception,match="requires fresh"):
        test_claims_public_75512be_frozen("sample-01")


@pytest.mark.parametrize("stamp",[None,"0"*64])
def test_public_result_requires_matching_code_sha(tmp_path,monkeypatch,stamp):
    from tests.test_claims_p31f_frozen import test_claims_public_75512be_frozen
    (tmp_path/"sample-01.json").write_text(json.dumps({"code_sha256":stamp}),encoding="utf-8")
    monkeypatch.setenv("SMTW_CLAIMS_PUBLIC_RESULTS",str(tmp_path))
    with pytest.raises(AssertionError,match="code SHA mismatch"):
        test_claims_public_75512be_frozen("sample-01")
