import json
from pathlib import Path

import pytest

from smtw.claims import judge


@pytest.mark.parametrize("name", [
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "anthropic_model", "CLAUDE_CODE_USE_BEDROCK", "claude_code_use_vertex",
])
def test_provider_override_rejected_without_value_lookup(name):
    class NamesOnly(dict):
        def __getitem__(self, key):
            pytest.fail("environment value was looked up")
    with pytest.raises(ValueError, match="provider override"):
        judge.child_environment(NamesOnly({name: object()}))


def test_allowlist_never_looks_up_unapproved_values():
    forbidden = {"AWS_SECRET_ACCESS_KEY", "AZURE_CLIENT_SECRET", "DB_PASSWORD", "GITHUB_PAT",
                 "NPM_CONFIG__AUTH", "SOME_SECRET", "HTTP_PROXY", "NODE_OPTIONS",
                 "LC_SECRET", "OPENAI_API_KEY", "UNLISTED_VARIABLE"}
    class Checked(dict):
        def __getitem__(self, key):
            assert key not in forbidden
            return super().__getitem__(key)
    parent = Checked({key: object() for key in forbidden})
    parent.update(PATH="synthetic-path", SystemRoot="C:/synthetic/Windows", LANG="en_US.UTF-8")
    env = judge.child_environment(parent)
    assert set(env) == {"PATH", "SystemRoot", "LANG", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_PLUGIN_CACHE_DIR"}
    assert env["PATH"] == "synthetic-path"


@pytest.mark.parametrize("lean", [False, True])
def test_mcp_and_slash_isolation_are_always_on(lean):
    argv = judge.command(judge.SCHEMA.read_text(encoding="utf-8"), "claude-p", lean_context=lean)
    assert argv.count("--strict-mcp-config") == argv.count("--disable-slash-commands") == 1
    assert ("--system-prompt-file" in argv) is lean


@pytest.mark.parametrize("flags", [
    {"is_error": "true"}, {"is_error": "false"}, {"is_error": 1},
    {"is_error": ["invalid"]}, {"is_error": {"invalid": 1}},
    {"api_error_status": True}, {"api_error_status": False},
])
def test_malformed_error_envelopes_rejected_by_both_readers(flags):
    raw = json.dumps({"subtype": "success", "is_error": False, "api_error_status": None,
                      "structured_output": {"judgments": []}, **flags})
    assert judge._reported_error(raw)["error"] == "judge_reported_error"
    with pytest.raises(ValueError, match="judge reported"):
        judge._judgments(raw)


def test_global_launch_tripwire_applies_without_local_fixture():
    with pytest.raises(pytest.fail.Exception, match="global test tripwire"):
        judge._launch([], "", {})
