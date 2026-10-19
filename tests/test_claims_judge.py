import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from smtw.claims import judge as runner
from smtw.claims.packets import write_packets


@pytest.fixture(autouse=True)
def never_launch_model(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("REAL MODEL LAUNCH FORBIDDEN")
    monkeypatch.setattr(runner, "_launch", forbidden)
    monkeypatch.setattr(runner.subprocess, "run", forbidden)


def packet_folder(tmp_path):
    item = {"id":"q-001","claim":"The limit is 7.","before":"","after":"",
            "branch":"unverifiable","report_line":1,"redactions":{},
            "excerpts":[{"path":"~/show-me-the-work/smtw/example.py","ref":1,
                         "text":"1: MAX_LIMIT = 7","source":"transcript_read",
                         "revision":"","redactions":{}}]}
    folder = tmp_path / "packets"
    write_packets(folder,[{"version":1,"instructions":"data","data_block":[item]}],
                  {"case_id":"N-research-codex-today"})
    return folder


def fake_judge(argv, prompt, env):
    assert argv[1:8] == ["--model",runner.MODEL,"-p","--tools","","--no-session-persistence","--output-format"]
    rows=[{"id":"q-001","verdict":"supported","evidence_ref":1,
           "evidence_quote":"MAX_LIMIT = 7","rationale":"Literal excerpt."}]
    return SimpleNamespace(returncode=0,stdout=json.dumps({"structured_output":rows}))


def invoke(folder,tmp_path,**kw):
    options={"executor":fake_judge,"tty":lambda:True,
             "usage_reader":lambda:{"known":True,"five_hour":1,"weekly":2},
             "parent_env":{},"executable":"claude-p","output_root":tmp_path/"out"}
    options.update(kw)
    return runner.judge(folder,**options)


def test_fake_judge_verified_and_append_only_logs(tmp_path):
    folder=packet_folder(tmp_path)
    a=invoke(folder,tmp_path)
    b=invoke(folder,tmp_path)
    assert a["calls"]==b["calls"]==1
    logs=[json.loads(s) for s in (tmp_path/"out/calls.jsonl").read_text().splitlines()]
    assert len(logs)==2 and all(x["execution"]=="injected" for x in logs)
    result=json.loads((Path(a["run"])/"verified-001.json").read_text())
    assert result["counts"]["supported"]==1


@pytest.mark.parametrize("setting",[
    {"tty":lambda:False}, {"max_calls":0}, {"max_calls":11},
    {"parent_env":{"ANTHROPIC_API_KEY":""}},
    {"usage_reader":lambda:{"known":False}},
    {"usage_reader":lambda:{"known":True,"five_hour":60,"weekly":0}},
    {"usage_reader":lambda:{"known":True,"five_hour":0,"weekly":70}},
    {"executable":"claude"}, {"executable":"evil-claude-p"},
])
def test_preflight_refuses_before_executor(tmp_path,setting):
    folder=packet_folder(tmp_path)
    def fail(*args):pytest.fail("executor called on refused input")
    with pytest.raises(ValueError):invoke(folder,tmp_path,executor=fail,**setting)
    assert not (tmp_path/"out/calls.jsonl").exists()


def test_sensitive_environment_values_never_looked_up():
    class NamesOnly(dict):
        def __getitem__(self,key):
            if key.endswith(("_API_KEY","_TOKEN")):
                pytest.fail("sensitive value read")
            return super().__getitem__(key)
    env=NamesOnly(OPENAI_API_KEY="synthetic",OTHER_TOKEN="synthetic",PATH="allowed")
    assert runner.child_environment(env)=={"PATH":"allowed"}
    env["ANTHROPIC_API_KEY"]="synthetic"
    with pytest.raises(ValueError):runner.child_environment(env)


@pytest.mark.parametrize("mutation",["hash","missing","case","source","size","secret"])
def test_packet_validation_refuses_before_fake(tmp_path,mutation):
    folder=packet_folder(tmp_path)
    manifest=json.loads((folder/"manifest.json").read_text())
    path=folder/"packet-001.json"
    if mutation=="missing":path.unlink()
    elif mutation=="hash":path.write_text("{}")
    elif mutation=="case":manifest["case_id"]="excluded-customer"
    else:
        packet=json.loads(path.read_text())
        if mutation=="source":packet["data_block"][0]["excerpts"][0]["path"]="~/customer/task.md"
        elif mutation=="secret":packet["data_block"][0]["claim"]="password=synthetic1234"
        else:packet["padding"]="x"*30001
        data=json.dumps(packet).encode();path.write_bytes(data)
        manifest["packets"][0].update(sha256=runner.sha(data),bytes=len(data),characters=len(data.decode()))
    (folder/"manifest.json").write_text(json.dumps(manifest))
    def fail(*args):pytest.fail("executor called on invalid packet")
    with pytest.raises((ValueError,OSError)):invoke(folder,tmp_path,executor=fail)


def test_unknown_usage_requires_explicit_override(tmp_path):
    result=invoke(packet_folder(tmp_path),tmp_path,
                  usage_reader=lambda:{"known":False},allow_unknown_usage=True)
    assert result["unknown_usage_allowed"]


def test_usage_rechecked_between_calls(tmp_path):
    folder=packet_folder(tmp_path)
    usage=iter([{"known":True,"five_hour":0,"weekly":0},{"known":True,"five_hour":60,"weekly":0}])
    with pytest.raises(ValueError):invoke(folder,tmp_path,usage_reader=lambda:next(usage))


def test_usage_cache_shape_and_staleness(tmp_path):
    path=tmp_path/"usage.json"
    value={"services":{"claude_personal":{"as_of":time.time(),"error":None,
           "rows":[{"label":"5시간","used":12},{"label":"주간 전체","used":23}]}}}
    path.write_text(json.dumps(value))
    assert runner.read_usage(path)=={"known":True,"five_hour":12,"weekly":23}
    value["services"]["claude_personal"]["as_of"]=0
    path.write_text(json.dumps(value))
    assert runner.read_usage(path)=={"known":False}


def test_single_real_launcher_site_statically():
    import ast
    tree=ast.parse(Path(runner.__file__).read_text(encoding="utf-8"))
    sites=[node for node in ast.walk(tree) if isinstance(node,ast.Call)
           and isinstance(node.func,ast.Attribute)
           and isinstance(node.func.value,ast.Name) and node.func.value.id=="subprocess"]
    assert len(sites)==1 and sites[0].func.attr=="run"
    # Autouse fatal guards protect both this site and its subprocess from tests.


def test_nonzero_and_malformed_output_are_logged_without_stderr(tmp_path):
    folder=packet_folder(tmp_path)
    def fake(*args):return SimpleNamespace(returncode=7,stdout="{}",stderr="synthetic-private-note")
    with pytest.raises(ValueError):invoke(folder,tmp_path,executor=fake)
    log=(tmp_path/"out/calls.jsonl").read_text()
    assert "synthetic-private-note" not in log and '"exit_code": 7' in log
