import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from smtw.claims import judge as runner
from smtw.claims.packets import write_packets
from tests.claims_judge_support import make_scope


@pytest.fixture(autouse=True)
def never_launch_model(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("REAL MODEL LAUNCH FORBIDDEN")
    monkeypatch.setattr(runner, "_launch", forbidden)
    monkeypatch.setattr(runner.subprocess, "run", forbidden)


def packet_folder(tmp_path):
    item = {"id":"q-001","claim":"The limit is 7.","before":"","after":"",
            "branch":"unverifiable","report_line":1,"redactions":{},
            "excerpts":[{"path":"~/example-repo/src/example.py","ref":1,
                         "text":"1: MAX_LIMIT = 7","source":"transcript_read",
                         "revision":"","redactions":{}}]}
    folder = tmp_path / "packets"
    write_packets(folder,[{"version":1,"instructions":"data","data_block":[item]}],
                  {"case_id":"example-case"})
    return folder


def fake_judge(argv, prompt, env):
    assert argv[1:8] == ["--model",runner.MODEL,"-p","--tools","","--no-session-persistence","--output-format"]
    rows=[{"id":"q-001","verdict":"supported","evidence_ref":1,
           "evidence_quote":"MAX_LIMIT = 7","rationale":"Literal excerpt."}]
    return SimpleNamespace(returncode=0,stdout=json.dumps({"type":"result","subtype":"success","is_error":False,
               "api_error_status":None,"structured_output":{"judgments":rows}}))


def invoke(folder,tmp_path,**kw):
    options={"executor":fake_judge,"tty":lambda:True,
             "usage_reader":lambda:{"known":True,"five_hour":1,"weekly":2},
             "parent_env":{},"executable":"claude-p","output_root":tmp_path/"out","scope_file":make_scope(tmp_path)}
    options.update(kw)
    return runner.judge(folder,**options)


def test_fake_judge_verified_and_append_only_logs(tmp_path):
    folder=packet_folder(tmp_path)
    a=invoke(folder,tmp_path)
    b=invoke(folder,tmp_path)
    assert a["calls"]==b["calls"]==1
    logs=[json.loads(s) for s in (tmp_path/"out/calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(logs)==2 and all(x["execution"]=="injected" for x in logs)
    assert all(x["scope_sha256"]==runner.sha(make_scope(tmp_path).read_bytes()) for x in logs)
    result=json.loads((Path(a["run"])/"verified-001.json").read_text(encoding="utf-8"))
    assert result["counts"]["supported"]==1


@pytest.mark.parametrize("setting",[
    {"tty":lambda:False}, {"max_calls":0}, {"max_calls":11},
    {"parent_env":{"ANTHROPIC_API_KEY":""}},
    {"usage_reader":lambda:{"known":False}},
    {"usage_reader":lambda:{"known":True,"five_hour":40,"weekly":0}},
    {"usage_reader":lambda:{"known":True,"five_hour":0,"weekly":55}},
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
    manifest=json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
    path=folder/"packet-001.json"
    if mutation=="missing":path.unlink()
    elif mutation=="hash":path.write_text("{}")
    elif mutation=="case":manifest["case_id"]="excluded-customer"
    else:
        packet=json.loads(path.read_text(encoding="utf-8"))
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
    usage=iter([{"known":True,"five_hour":0,"weekly":0},{"known":True,"five_hour":40,"weekly":0}])
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
    log=(tmp_path/"out/calls.jsonl").read_text(encoding="utf-8")
    assert "synthetic-private-note" not in log and '"exit_code": 7' in log


def test_prompt_schema_hashes_frozen_in_manifest_and_call(tmp_path):
    from smtw.claims.judge_contract import contract
    folder=packet_folder(tmp_path)
    manifest=json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
    assert manifest["judge_contract"]==contract()
    invoke(folder,tmp_path)
    row=json.loads((tmp_path/"out/calls.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["prompt_sha256"]==contract()["prompt_sha256"]
    assert row["schema_sha256"]==contract()["schema_sha256"]
    assert row["cli_schema_sha256"]==contract()["cli_schema_sha256"]
    assert row["cli_schema_sha256"]==runner.sha(row["argv"][-1].encode("utf-8"))
    assert contract()["version"]=="v1.2"
    assert runner.SCHEMA.name=="judge-output.schema.json"


@pytest.mark.parametrize("field",["prompt_sha256","schema_sha256","cli_schema_sha256","version"])
def test_changed_contract_refuses_before_executor(tmp_path,field):
    folder=packet_folder(tmp_path)
    path=folder/"manifest.json";manifest=json.loads(path.read_text(encoding="utf-8"))
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
    schema=json.loads(runner.SCHEMA.read_text(encoding="utf-8"))
    assert schema["items"]["properties"]["evidence_quote"]["maxLength"]==200
    assert schema["items"]["properties"]["rationale"]["maxLength"]==120


def test_compact_cli_schema_retains_array_constraints_and_empty_tools():
    original=runner.SCHEMA.read_text(encoding="utf-8")
    argv=runner.command(original,"claude-p.cmd")
    assert "\n" not in argv[-1] and "\r" not in argv[-1]
    expected=json.loads(original);expected.pop("$schema")
    assert json.loads(argv[-1])["properties"]["judgments"]==expected
    assert json.loads(argv[-1])["type"]=="object"
    assert argv[argv.index("--tools")+1]==""
    assert argv[argv.index("--model")+1]==runner.MODEL


def test_native_executable_discovery_uses_files_only(tmp_path,monkeypatch):
    binary=tmp_path/"node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    binary.parent.mkdir(parents=True);binary.write_bytes(b"fake only")
    monkeypatch.setattr(runner,"os",SimpleNamespace(name="nt"))
    monkeypatch.setattr(runner.shutil,"which",lambda name:str(tmp_path/"claude.cmd") if name=="claude" else None)
    assert runner.resolve_executable()==str(binary.resolve())


@pytest.mark.parametrize("stamp,known",[
    (1_700_000_000,True),(1_700_000_000_000,True),
    (1_699_999_399,False),(1_699_999_399_000,False),
    (1_700_000_061_000,False),(0,False),(True,False),
    (float("nan"),False),(float("inf"),False),
])
def test_usage_widget_milliseconds_seconds_and_invalid_clocks(tmp_path,monkeypatch,stamp,known):
    monkeypatch.setattr(runner.time,"time",lambda:1_700_000_000)
    cache=tmp_path/"usage.json"
    value={"services":{"claude_personal":{"as_of":stamp,"error":None,
           "rows":[{"label":"5시간","used":59},{"label":"주간 전체","used":69}]}}}
    cache.write_text(json.dumps(value),encoding="utf-8")
    result=runner.read_usage(cache)
    assert result["known"] is known
    if known:assert result=={"known":True,"five_hour":59,"weekly":69}


@pytest.mark.parametrize("missing", ["scope_file", "output_root"])
def test_judge_api_requires_explicit_scope_and_output(tmp_path, missing):
    options = {"scope_file": make_scope(tmp_path), "output_root": tmp_path / "out"}
    del options[missing]
    with pytest.raises(TypeError):
        runner.judge(packet_folder(tmp_path), **options)


@pytest.mark.parametrize("argv", [
    ["--packets", "synthetic-packets"],
    ["--packets", "synthetic-packets", "--out-root", "synthetic-output"],
    ["--packets", "synthetic-packets", "--scope-file", "synthetic-scope.json"],
])
def test_cli_requires_scope_and_output_before_any_execution(argv):
    with pytest.raises(SystemExit) as error:
        runner.main(argv)
    assert error.value.code == 2


def test_scope_hash_mismatch_refuses_before_fake(tmp_path):
    folder = packet_folder(tmp_path)
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["scope_sha256"] = "0" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    def fail(*args):
        pytest.fail("changed scope executed")
    with pytest.raises(ValueError, match="scope changed"):
        invoke(folder, tmp_path, executor=fail)



def test_cli_schema_wraps_array_and_preserves_phase1_constraints():
    original = json.loads(runner.SCHEMA.read_text(encoding="utf-8"))
    schema = {**original, "$id": "urn:synthetic:judgments", "$comment": "local metadata"}
    delivered = json.loads(runner.command(json.dumps(schema), "claude-p")[-1])
    expected = {key: value for key, value in original.items() if key != "$schema"}
    assert delivered == {"type": "object", "properties": {"judgments": expected},
                         "required": ["judgments"], "additionalProperties": False}
    assert "$schema" in original and original["type"] == "array"
    assert "$schema" not in json.dumps(delivered) and "$id" not in json.dumps(delivered)


@pytest.mark.parametrize("envelope", ["structured_output", "result"])
def test_wrapped_judgments_reach_existing_quote_verifier(tmp_path, envelope):
    rows = [{"id": "q-001", "verdict": "supported", "evidence_ref": 1,
             "evidence_quote": "MAX_LIMIT = 7", "rationale": "Literal source text."}]
    payload = {"judgments": rows}
    def injected(*args):
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "subtype": "success", envelope: json.dumps(payload) if envelope == "result" else payload}))
    result = invoke(packet_folder(tmp_path), tmp_path, executor=injected)
    output = json.loads((Path(result["run"]) / "verified-001.json").read_text(encoding="utf-8"))
    assert output["counts"]["supported"] == 1
    assert json.loads((Path(result["run"]) / "judgments-001.json").read_text(encoding="utf-8")) == rows


@pytest.mark.parametrize("flags", [{"is_error": True}, {"api_error_status": 400}, {"is_error": True, "api_error_status": 400}])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_cli_api_errors_preserve_raw_and_record_bounded_reason(tmp_path, flags, exit_code):
    reason = "API Error: 400 input_schema.type must be object. " + "detail " * 50
    raw = json.dumps({"subtype": "success", **flags, "result": reason}) + "\n"
    calls = []
    def injected(*args):
        calls.append(1)
        return SimpleNamespace(returncode=exit_code, stdout=raw, stderr="environment text must not be logged")
    with pytest.raises(ValueError):
        invoke(packet_folder(tmp_path), tmp_path, executor=injected)
    assert calls == [1]
    row, = [json.loads(line) for line in (tmp_path / "out/calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert row["error"] == "judge_reported_error"
    assert row["error_reason"] == reason[:200]
    assert row.get("api_error_status") == flags.get("api_error_status")
    raw_file, = (tmp_path / "out/judge-runs").glob("*/raw-001.json")
    assert raw_file.read_bytes() == raw.encode("utf-8")
    assert row["output_sha256"] == runner.sha(raw_file.read_bytes())
    assert not list((tmp_path / "out/judge-runs").glob("*/judgments-*.json"))
    assert "environment text" not in json.dumps(row)


def test_error_reason_masks_synthetic_keyed_value_without_environment_lookup(tmp_path):
    value = "synthetic_only_value_" * 3
    key = "EXAMPLE_" + "TOKEN"
    raw = json.dumps({"is_error": True, "result": f"API error: {key}={value}"})
    def injected(*args):
        return SimpleNamespace(returncode=0, stdout=raw)
    with pytest.raises(ValueError):
        invoke(packet_folder(tmp_path), tmp_path, executor=injected)
    row = json.loads((tmp_path / "out/calls.jsonl").read_text(encoding="utf-8"))
    assert row["error"] == "judge_reported_error"
    assert value not in row["error_reason"] and len(row["error_reason"]) <= 200



def test_cli_api_error_stops_before_the_next_packet(tmp_path):
    original = packet_folder(tmp_path)
    first = json.loads((original / "packet-001.json").read_text(encoding="utf-8"))
    second = json.loads(json.dumps(first))
    second["data_block"][0]["id"] = "q-002"
    folder = tmp_path / "two-packets"
    write_packets(folder, [first, second], {"case_id": "example-case"})
    calls = []
    def injected(*args):
        calls.append(1)
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "is_error": True, "api_error_status": 400, "result": "Schema rejected."}))
    with pytest.raises(ValueError):
        invoke(folder, tmp_path, executor=injected)
    assert calls == [1]
    assert len((tmp_path / "out/calls.jsonl").read_text(encoding="utf-8").splitlines()) == 1

# Fixed synthetic CLI envelope: no captured model response or credentials.
SUCCESS_NULL_STATUS = """{
  "type": "result", "subtype": "success", "is_error": false,
  "api_error_status": null, "duration_ms": 123, "result": "",
  "usage": {"input_tokens": 11, "output_tokens": 22,
            "cache_read_input_tokens": 33, "cache_creation_input_tokens": 44},
  "structured_output": {"judgments": [
    {"id": "q-001", "verdict": "supported", "evidence_ref": 1,
     "evidence_quote": "MAX_LIMIT = 7", "rationale": "Synthetic literal source."}
  ]}
}"""


def test_success_with_null_api_status_writes_verified_file(tmp_path):
    def injected(*args):
        return SimpleNamespace(returncode=0, stdout=SUCCESS_NULL_STATUS)
    result = invoke(packet_folder(tmp_path), tmp_path, executor=injected)
    verified = json.loads((Path(result["run"]) / "verified-001.json").read_text(encoding="utf-8"))
    assert verified["counts"]["supported"] == 1
    assert verified["items"][0]["quote_verified"]
    receipt = json.loads((tmp_path / "out/calls.jsonl").read_text(encoding="utf-8"))
    assert receipt["error"] is None
    assert (Path(result["run"]) / "raw-001.json").read_bytes() == SUCCESS_NULL_STATUS.encode("utf-8")


@pytest.mark.parametrize("overrides", [
    {"is_error": True}, {"api_error_status": 400}, {"api_error_status": 0},
    {"subtype": "error_max_turns"}, {"subtype": None}, {"subtype": "missing"},
])
def test_both_response_readers_reject_error_envelopes(tmp_path, overrides):
    value = json.loads(SUCCESS_NULL_STATUS)
    value.update(overrides)
    if overrides.get("subtype") == "missing":
        del value["subtype"]
    raw = json.dumps(value)
    assert runner._reported_error(raw)["error"] == "judge_reported_error"
    with pytest.raises(ValueError, match="judge reported an error"):
        runner._judgments(raw)
    with pytest.raises(ValueError, match="judge execution failed"):
        invoke(packet_folder(tmp_path), tmp_path,
               executor=lambda *args: SimpleNamespace(returncode=0, stdout=raw))
    receipt = json.loads((tmp_path / "out/calls.jsonl").read_text(encoding="utf-8"))
    assert receipt["error"] == "judge_reported_error"
    assert not list((tmp_path / "out/judge-runs").glob("*/verified-*.json"))


@pytest.mark.parametrize("status", [None, "400"])
def test_only_integer_api_status_is_an_error(status):
    value = json.loads(SUCCESS_NULL_STATUS)
    value["api_error_status"] = status
    raw = json.dumps(value)
    assert runner._reported_error(raw) == {}
    assert runner._judgments(raw) == value["structured_output"]["judgments"]


def test_legacy_direct_judgment_array_remains_supported():
    rows = json.loads(SUCCESS_NULL_STATUS)["structured_output"]["judgments"]
    raw = json.dumps(rows)
    assert runner._reported_error(raw) == {}
    assert runner._judgments(raw) == rows

def test_lean_disabled_preserves_argv_bytes_and_executor_signature(tmp_path):
    schema = runner.SCHEMA.read_text(encoding="utf-8")
    baseline = ["claude-p", "--model", "claude-sonnet-5-5", "-p", "--tools", "",
                "--no-session-persistence", "--output-format", "json",
                "--disable-slash-commands", "--strict-mcp-config", "--json-schema",
                runner.cli_schema(schema)]
    encode = lambda argv: json.dumps(argv, ensure_ascii=True).encode("ascii")
    assert encode(runner.command(schema, "claude-p")) == encode(baseline)
    assert encode(runner.command(schema, "claude-p", lean_context=False)) == encode(baseline)
    before = Path.cwd()
    def injected(argv, prompt, env):
        assert argv == baseline and Path.cwd() == before
        return SimpleNamespace(returncode=0, stdout=SUCCESS_NULL_STATUS)
    invoke(packet_folder(tmp_path), tmp_path, executor=injected)
    record = json.loads((tmp_path / "out/calls.jsonl").read_text(encoding="utf-8"))
    assert record["lean_context"] is False
    assert record["usage"] == json.loads(SUCCESS_NULL_STATUS)["usage"]


def test_lean_each_packet_has_new_empty_cwd_and_removes_it(tmp_path):
    original = packet_folder(tmp_path)
    first = json.loads((original / "packet-001.json").read_text(encoding="utf-8"))
    second = json.loads(json.dumps(first))
    second["data_block"][0]["id"] = "q-002"
    folder = tmp_path / "two-packets"
    write_packets(folder, [first, second], {"case_id": "example-case"})
    visited, parent_cwd = [], Path.cwd()
    baseline = runner.command(runner.SCHEMA.read_text(encoding="utf-8"), "claude-p")
    def injected(argv, prompt, env, *, cwd):
        assert Path.cwd() == parent_cwd
        directory = Path(cwd)
        assert directory.is_absolute()
        assert directory.parent == Path(runner.tempfile.gettempdir())
        assert list(directory.iterdir()) == []
        assert all(not old.exists() for old in visited)
        visited.append(directory)
        assert argv == baseline + ["--system-prompt-file", str(runner.PROMPT.resolve())]
        assert argv[argv.index("--tools") + 1] == ""
        assert not {"--bare", "--mcp-config", "--setting-sources",
                    "--dangerously-skip-permissions", "--permission-mode"}.intersection(argv)
        assert "ANTHROPIC_API_KEY" not in env
        (directory / "synthetic-child-output.txt").write_text("synthetic only", encoding="utf-8")
        response = json.loads(SUCCESS_NULL_STATUS)
        response["structured_output"]["judgments"][0]["id"] = "q-" + str(len(visited)).zfill(3)
        return SimpleNamespace(returncode=0, stdout=json.dumps(response))
    result = invoke(folder, tmp_path, executor=injected, lean_context=True)
    assert result["calls"] == 2
    assert len(set(visited)) == 2 and all(not p.exists() for p in visited)
    records = [json.loads(line) for line in (tmp_path / "out/calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(row["lean_context"] is True for row in records)
    assert all(row["usage"] == json.loads(SUCCESS_NULL_STATUS)["usage"] for row in records)
    assert len(list(Path(result["run"]).glob("verified-*.json"))) == 2
    assert Path.cwd() == parent_cwd


@pytest.mark.parametrize("failure", ["exception", "timeout", "nonzero", "reported"])
def test_lean_cleans_cwd_on_failure_and_records_mode(tmp_path, failure):
    visited, before = [], Path.cwd()
    def injected(argv, prompt, env, *, cwd):
        directory = Path(cwd)
        assert list(directory.iterdir()) == []
        visited.append(directory)
        (directory / "synthetic.txt").write_text("synthetic only", encoding="utf-8")
        if failure == "exception":
            raise RuntimeError("synthetic error")
        if failure == "timeout":
            raise runner.subprocess.TimeoutExpired("synthetic", 1)
        response = json.loads(SUCCESS_NULL_STATUS)
        if failure == "reported":
            response["is_error"] = True
        return SimpleNamespace(returncode=7 if failure == "nonzero" else 0, stdout=json.dumps(response))
    with pytest.raises(ValueError):
        invoke(packet_folder(tmp_path), tmp_path, executor=injected, lean_context=True)
    assert len(visited) == 1 and not visited[0].exists()
    assert Path.cwd() == before
    record = json.loads((tmp_path / "out/calls.jsonl").read_text(encoding="utf-8"))
    assert record["lean_context"] is True and record["error"]
    assert "synthetic error" not in json.dumps(record)


@pytest.mark.parametrize("lean", [False, True])
def test_lean_cli_is_explicit_and_defaults_off(monkeypatch, lean):
    seen = []
    def injected(folder, **kwargs):
        seen.append(kwargs)
        return {"execution": "injected"}
    monkeypatch.setattr(runner, "judge", injected)
    args = ["--packets", "synthetic-packets", "--scope-file", "synthetic-scope.json",
            "--out-root", "synthetic-output", "--config-dir", "synthetic-config",
            "--usage-service", "claude"]
    assert runner.main(args + (["--lean-context"] if lean else [])) == 0
    assert seen[0]["lean_context"] is lean
    assert seen[0]["config_dir"] == "synthetic-config" and seen[0]["usage_service"] == "claude"


@pytest.mark.parametrize("lean", [False, True])
@pytest.mark.parametrize("flag", ["--bare", "--setting-sources", "--permission-mode",
                                 "--dangerously-skip-permissions", "--mcp-config",
                                 "--disable-slash-commands", "--strict-mcp-config", "--system-prompt-file"])
def test_extra_cli_flags_are_not_operator_passthrough(lean, flag):
    args = ["--packets", "synthetic-packets", "--scope-file", "synthetic-scope.json",
            "--out-root", "synthetic-output", "--config-dir", "synthetic-config",
            "--usage-service", "claude", flag]
    with pytest.raises(SystemExit) as error:
        runner.main(args + (["--lean-context"] if lean else []))
    assert error.value.code == 2


@pytest.mark.parametrize("lean", [False, True])
def test_bare_cannot_be_passed_as_an_executable(lean):
    with pytest.raises(ValueError):
        runner.command(runner.SCHEMA.read_text(encoding="utf-8"), "--bare", lean_context=lean)


@pytest.mark.parametrize("raw", ["invalid json", "[]", "{}", '{"usage": null}',
                               '{"usage": {"input_tokens": true, "output_tokens": -1, "cache_read_input_tokens": "12"}}'])
def test_usage_copies_only_nonnegative_integer_counters(raw):
    assert runner._token_usage(raw) == {
        "input_tokens": None, "output_tokens": None,
        "cache_read_input_tokens": None, "cache_creation_input_tokens": None}


@pytest.mark.parametrize("lean", ["false", 1, None])
def test_lean_context_requires_a_boolean(lean):
    with pytest.raises(ValueError, match="boolean"):
        runner.command(runner.SCHEMA.read_text(encoding="utf-8"), "claude-p", lean_context=lean)
