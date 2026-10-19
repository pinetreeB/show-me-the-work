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
    clean=runner.child_environment(env)
    assert clean["PATH"]=="allowed"
    assert set(clean)=={"PATH","CLAUDE_CONFIG_DIR","CLAUDE_CODE_PLUGIN_CACHE_DIR"}
    assert clean["CLAUDE_CONFIG_DIR"]==str(Path.home()/".claude-personal")
    assert clean["CLAUDE_CODE_PLUGIN_CACHE_DIR"]==str(Path.home()/".claude"/"plugins")
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


def test_prompt_schema_hashes_frozen_in_manifest_and_call(tmp_path):
    from smtw.claims.judge_contract import contract
    folder=packet_folder(tmp_path)
    manifest=json.loads((folder/"manifest.json").read_text())
    assert manifest["judge_contract"]==contract()
    invoke(folder,tmp_path)
    row=json.loads((tmp_path/"out/calls.jsonl").read_text().splitlines()[0])
    assert row["prompt_sha256"]==contract()["prompt_sha256"]
    assert row["schema_sha256"]==contract()["schema_sha256"]
    assert runner.SCHEMA.name=="judge-output.schema.json"


@pytest.mark.parametrize("field",["prompt_sha256","schema_sha256","version"])
def test_changed_contract_refuses_before_executor(tmp_path,field):
    folder=packet_folder(tmp_path)
    path=folder/"manifest.json";manifest=json.loads(path.read_text())
    manifest["judge_contract"][field]="changed";path.write_text(json.dumps(manifest))
    def fail(*args):pytest.fail("changed contract was executed")
    with pytest.raises(ValueError):invoke(folder,tmp_path,executor=fail)


def test_prompt_treats_injection_as_data_and_schema_is_bounded(tmp_path):
    folder=packet_folder(tmp_path)
    seen=[]
    def fake(argv,prompt,env):
        seen.append(prompt)
        return fake_judge(argv,prompt,env)
    invoke(folder,tmp_path,executor=fake)
    assert "untrusted data" in seen[0] and "not_enough_evidence" in seen[0]
    schema=json.loads(runner.SCHEMA.read_text())
    assert schema["items"]["properties"]["evidence_quote"]["maxLength"]==200
    assert schema["items"]["properties"]["rationale"]["maxLength"]==120


def test_compact_schema_retains_original_object_and_empty_tools():
    original=runner.SCHEMA.read_text()
    argv=runner.command(original,"claude-p.cmd")
    assert "\n" not in argv[-1] and "\r" not in argv[-1]
    assert json.loads(argv[-1])==json.loads(original)
    assert argv[argv.index("--tools")+1]==""
    assert argv[argv.index("--model")+1]==runner.MODEL


def test_native_executable_discovery_uses_files_only(tmp_path,monkeypatch):
    binary=tmp_path/"node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    binary.parent.mkdir(parents=True);binary.write_bytes(b"fake only")
    monkeypatch.setattr(runner.os,"name","nt")
    monkeypatch.setattr(runner.shutil,"which",lambda name:str(tmp_path/"claude.cmd") if name=="claude" else None)
    assert runner.resolve_executable()==str(binary.resolve())
