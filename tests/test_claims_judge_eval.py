import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from smtw.claims.advisory import load_sent_items,verify_judgments
from smtw.claims.judge_eval import generate,score,score_table,summarize
from smtw.claims import judge


@pytest.fixture(autouse=True)
def no_real_launcher(monkeypatch):
    def fail(*args,**kw):pytest.fail("REAL MODEL EXECUTION FORBIDDEN")
    monkeypatch.setattr(judge,"_launch",fail)
    monkeypatch.setattr(judge.subprocess,"run",fail)


def pool():
    return [{"case":"P-pilot-agy-research","path":"~/show-me-the-work/sample.py",
             "line":1,"quote":"MAX_LIMIT = 7","window":{1:"MAX_LIMIT = 7",2:"MIN_LIMIT = 1"},
             "text":"1: MAX_LIMIT = 7\n2: MIN_LIMIT = 1","redactions":{},
             "report_line":10,"observation":"synthetic"}]


def make_run(path,sent,truth,*,wrong=False,bad_quote=False):
    path.mkdir()
    rows=[]
    for index,(key,value) in enumerate(truth.items()):
        verdict=value["verdict"]
        if wrong and index==0:verdict="contradicted" if verdict=="supported" else "supported"
        rows.append({"id":key,"verdict":verdict,"rationale":"Synthetic oracle fixture.",
                     "evidence_ref":1,"evidence_quote":"made up quote" if bad_quote and index==0 else "MAX_LIMIT = 7"})
    (path/"judgments-001.json").write_text(json.dumps(rows))
    (path/"verified-001.json").write_text(json.dumps(verify_judgments(rows,sent)))
    return path


def test_200_paired_items_truth_separation_and_no_answer_hints(tmp_path):
    result=generate(pool(),tmp_path/"eval")
    assert result["items"]==200 and result["unique_originals"]==1 and result["model_calls"]==0
    folder=tmp_path/"eval/packets";sent=load_sent_items(folder)
    truth=json.loads((tmp_path/"eval/truth/answers.json").read_text(encoding="utf-8"))
    assert sum(v["verdict"]=="supported" for v in truth["items"].values())==100
    assert sum(v["verdict"]=="contradicted" for v in truth["items"].values())==100
    assert set(truth["items"])==set(sent)
    forbidden={"truth","expected","verdict","mutation","is_original","answers","sampling"}
    def walk(value):
        if isinstance(value,dict):
            assert not forbidden.intersection(value)
            for child in value.values():walk(child)
        elif isinstance(value,list):
            for child in value:walk(child)
    for file in folder.glob("*.json"):walk(json.loads(file.read_text(encoding="utf-8")))
    assert {p.name for p in folder.iterdir()}=={"manifest.json",*[p.name for p in folder.glob("packet-*.json")]}
    assert "| Packet | Items |" in summarize(folder)


def test_outside_case_and_source_rejected(tmp_path):
    for field,value in [("case","P-yj-1015-gap"),("path","~/scoopridersapp/task.py")]:
        rows=pool();rows[0][field]=value
        with pytest.raises(ValueError):generate(rows,tmp_path/field)


def test_scores_and_three_run_agreement_are_from_verified_outputs(tmp_path):
    generate(pool(),tmp_path/"eval",per_kind=1)
    folder=tmp_path/"eval/packets";sent=load_sent_items(folder)
    truth_file=tmp_path/"eval/truth/answers.json";truth=json.loads(truth_file.read_text(encoding="utf-8"))["items"]
    runs=[make_run(tmp_path/f"r{i}",sent,truth,wrong=i==2) for i in range(3)]
    result=score(folder,truth_file,runs)
    assert result["runs"][0]["recall"]==1 and result["runs"][0]["precision"]==1
    assert result["runs"][2]["false_positive"]==1
    assert result["runs"][2]["false_display_rate"]==0.2
    assert result["verdict_agreement_3"]==0.9
    assert result["verified_quote_agreement_3"]==1
    assert score_table(result).splitlines()[0]=="| Run | Recall | Precision | False display | Quote not in excerpt | Unjudged |"


def test_bad_quote_not_displayed_and_forged_verifier_rejected(tmp_path):
    generate(pool(),tmp_path/"eval",per_kind=1)
    folder=tmp_path/"eval/packets";sent=load_sent_items(folder)
    truth_file=tmp_path/"eval/truth/answers.json";truth=json.loads(truth_file.read_text(encoding="utf-8"))["items"]
    run=make_run(tmp_path/"run",sent,truth,bad_quote=True)
    assert score(folder,truth_file,[run])["runs"][0]["quote_not_in_excerpt_rate"]==0.1
    path=run/"verified-001.json";value=json.loads(path.read_text(encoding="utf-8"));value["counts"]["quote_verified"]+=1
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):score(folder,truth_file,[run])


def test_all_wrapper_calls_in_pipeline_use_injected_fake(tmp_path):
    generate(pool(),tmp_path/"eval",per_kind=1)
    calls=[]
    def fake(argv,prompt,env):
        calls.append(argv)
        data=json.loads(prompt.split("PACKET DATA\n",1)[1])
        rows=[{"id":item["id"],"verdict":"not_enough_evidence","rationale":"Fake pipeline only."}
              for item in data["data_block"]]
        return SimpleNamespace(returncode=0,stdout=json.dumps({"structured_output":rows}))
    result=judge.judge(tmp_path/"eval/packets",executor=fake,tty=lambda:True,
        parent_env={},usage_reader=lambda:{"known":True,"five_hour":0,"weekly":0},
        executable="claude-p",output_root=tmp_path/"calls")
    assert len(calls)==result["calls"] and result["execution"]=="injected"
