"""Windows argv transport uses only a locally compiled echo executable."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from smtw.claims import judge

pytestmark=pytest.mark.skipif(os.name!="nt",reason="Windows cmd.exe argument transport")


@pytest.fixture
def echo_chain(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):pytest.fail("REAL JUDGE LAUNCH FORBIDDEN")
    monkeypatch.setattr(judge,"_launch",forbidden)
    compiler=Path(os.environ.get("WINDIR",r"C:\Windows"))/"Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.exists():pytest.skip("local .NET Framework compiler unavailable")
    folder=tmp_path/"echo with spaces";folder.mkdir()
    source=folder/"Echo.cs"
    source.write_text(r"""using System;
using System.Web.Script.Serialization;
class Echo {
 static void Main(string[] args) {
  Console.WriteLine(new JavaScriptSerializer().Serialize(new {
   argv=args, config=Environment.GetEnvironmentVariable("CLAUDE_CONFIG_DIR"),
   plugins=Environment.GetEnvironmentVariable("CLAUDE_CODE_PLUGIN_CACHE_DIR")
  }));
 }
}""",encoding="utf-8")
    binary=folder/"claude.exe"
    subprocess.run([str(compiler),"/nologo","/r:System.Web.Extensions.dll",
                    "/out:"+str(binary),str(source)],capture_output=True,check=True)
    second=folder/"npm-claude.cmd"
    second.write_text('@echo off\n"'+str(binary)+'" %*\n',encoding="ascii")
    first=folder/"claude-p.cmd"
    first.write_text('@echo off\ncall "'+str(second)+'" %*\n',encoding="ascii")
    return binary,first


@pytest.mark.parametrize("route",[0,1],ids=["fake-native-exe","fake-cmd-two-stage"])
def test_schema_tools_and_model_roundtrip_exactly(echo_chain,route):
    executable=echo_chain[route]
    original=judge.SCHEMA.read_text(encoding="utf-8")
    argv=judge.command(original,str(executable))
    env=judge.child_environment({name:os.environ[name] for name in
        ["SystemRoot","WINDIR","COMSPEC","PATH","TEMP","TMP"] if name in os.environ})
    assert executable.is_relative_to(echo_chain[0].parent)
    # Explicit fake transport executor; neither _launch nor any installed model runs.
    completed=subprocess.run(argv,capture_output=True,env=env,encoding="utf-8",check=True)
    echoed=json.loads(completed.stdout)
    assert echoed["argv"]==argv[1:]
    actual=echoed["argv"]
    assert json.loads(actual[actual.index("--json-schema")+1])==json.loads(original)
    assert actual[actual.index("--json-schema")+1]==json.dumps(
        json.loads(original),ensure_ascii=True,separators=(",",":"))
    assert actual[actual.index("--tools")+1]==""
    assert actual[actual.index("--model")+1]=="claude-sonnet-5-5"
    assert echoed["config"]==str(Path.home()/".claude-personal")
    assert echoed["plugins"]==str(Path.home()/".claude"/"plugins")
