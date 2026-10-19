"""Offline CLI-to-judge scope contract using only synthetic repositories."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from smtw.claims import judge
from smtw.claims.advisory import load_sent_items
from smtw.claims.judge_scope import load_scope
from smtw.claims.packets import display_path

CASE = "scope-only-example"


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("REAL MODEL LAUNCH FORBIDDEN")
    monkeypatch.setattr(judge, "_launch", forbidden)


def cli(*args):
    return subprocess.run([sys.executable, "-B", "-m", "smtw", "claims", "assist", *map(str, args)],
                          capture_output=True, encoding="utf-8", timeout=30)


def synthetic_case(tmp_path, kind):
    repo = tmp_path / "synthetic-repo"
    repo.mkdir()
    hooks = tmp_path / "empty-hooks"
    hooks.mkdir()
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    def git(*args):
        result = subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=Synthetic",
             "-c", "user.email=synthetic@example.invalid", "-c", "commit.gpgsign=false",
             "-c", "core.hooksPath=" + str(hooks), *args],
            capture_output=True, encoding="utf-8", env=env, check=True)
        return result.stdout.strip()
    files = {"approved/alpha.py": "VALUE = 7\n",
             "outside/beta.py": "OUTSIDE_ONLY_MARKER = 8\n",
             "approved/keys/blocked.py": "SENSITIVE_PATH_MARKER = 9\n"}
    git("init", "-q")
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git("add", "--", *files)
    git("commit", "-qm", "synthetic scope fixture")
    revision = git("rev-parse", "HEAD")
    report = tmp_path / "report.md"
    report.write_text("\n".join("`" + name + ":1` 내용" for name in files), encoding="utf-8")
    rows = []
    for index, (name, content) in enumerate(files.items()):
        path = (repo / name).as_posix()
        if kind == "agy":
            rows += [
                {"type": "PLANNER_RESPONSE", "tool_calls": [
                    {"name": "view_file", "args": {"AbsolutePath": path}}]},
                {"type": "GENERIC", "status": "DONE",
                 "content": f"File Path: {path}\nTotal Lines: 1\nShowing lines 1 to 1\n1: {content.strip()}"},
            ]
        else:
            ident = "synthetic-read-" + str(index)
            rows += [
                {"type": "response_item", "payload": {
                    "type": "function_call", "name": "exec_command", "call_id": ident,
                    "arguments": json.dumps({"cmd": "Get-Content -LiteralPath " + json.dumps(path)})}},
                {"type": "response_item", "payload": {
                    "type": "function_call_output", "call_id": ident,
                    "output": json.dumps({"output": content, "exit_code": 0})}},
            ]
    transcript = tmp_path / (kind + ".jsonl")
    transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    scope_file = tmp_path / "scope.json"
    prefix = display_path((repo / "approved").as_posix()).rstrip("/") + "/"
    scope_file.write_text(json.dumps({"version": 1, "cases": {
        CASE: [prefix], "other-scope-example": [prefix]}, "repositories": []}), encoding="utf-8")
    folder = tmp_path / "packets"
    args = ["--report", report, "--" + kind, transcript, "--repo", repo,
            "--commit", str(repo) + "=" + revision, "--dry-run", "--out", folder,
            "--case-id", CASE, "--scope-file", scope_file, "--format", "json"]
    return args, folder, scope_file


@pytest.mark.parametrize("kind", ["agy", "codex"])
def test_assist_scope_cli_to_injected_judge_and_verifier(tmp_path, kind):
    args, folder, scope_file = synthetic_case(tmp_path, kind)
    completed = cli(*args)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    manifest = json.loads(completed.stdout)
    assert manifest == json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["case_id"] == CASE
    assert manifest["scope_sha256"] == hashlib.sha256(scope_file.read_bytes()).hexdigest()
    assert manifest["model_calls"] == 0 and manifest["sent_count"] == 1
    # One excluded transcript observation plus one pinned-commit source.
    assert manifest["skipped_out_of_scope"] == 2
    sent = load_sent_items(folder)
    excerpts = [e for item in sent.values() for e in item["excerpts"]]
    assert excerpts and all(e["path"].endswith("/approved/alpha.py") for e in excerpts)
    assert all(e["source"] == "transcript_read" for e in excerpts)
    assert all("OUTSIDE_ONLY_MARKER" not in e["text"] and
               "SENSITIVE_PATH_MARKER" not in e["text"] for e in excerpts)
    scope = load_scope(scope_file)
    judge.validate_scope(manifest, sent, scope)
    calls = []
    def fake(argv, prompt, env):
        calls.append(1)
        rows = []
        for item in sent.values():
            excerpt = item["excerpts"][0]
            rows.append({"id": item["id"], "verdict": "supported",
                         "evidence_ref": excerpt["ref"],
                         "evidence_quote": excerpt["text"].splitlines()[0].split(": ", 1)[1],
                         "rationale": "Synthetic literal excerpt."})
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "subtype": "success", "is_error": False, "api_error_status": None,
            "structured_output": {"judgments": rows}}))
    options = dict(scope_file=scope_file, output_root=tmp_path / "judge-output",
                   executor=fake, tty=lambda: True, parent_env={}, executable="claude-p",
                   usage_reader=lambda: {"known": True, "five_hour": 1, "weekly": 2})
    result = judge.judge(folder, **options)
    assert result["execution"] == "injected" and result["calls"] == 1 and calls == [1]
    verified = json.loads((Path(result["run"]) / "verified-001.json").read_text(encoding="utf-8"))
    assert verified["counts"]["quote_verified"] == 1
    verify_args = ["--verify", Path(result["run"]) / "judgments-001.json", "--packets", folder,
                   "--case-id", CASE, "--scope-file", scope_file, "--format", "json"]
    assert cli(*verify_args).returncode == 0
    wrong_case = list(verify_args)
    wrong_case[wrong_case.index(CASE)] = "other-scope-example"
    rejected = cli(*wrong_case)
    assert rejected.returncode == 2 and "case_id" in json.loads(rejected.stdout)["error"]
    scope_file.write_bytes(scope_file.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="scope changed"):
        judge.judge(folder, **options)
    assert calls == [1]
    rejected = cli(*verify_args)
    assert rejected.returncode == 2 and "scope changed" in json.loads(rejected.stdout)["error"]


@pytest.mark.parametrize("mode", ["--dry-run", "--select-only", "--verify"])
@pytest.mark.parametrize("missing", ["case", "scope"])
def test_scope_arguments_are_paired_before_input_reads(tmp_path, mode, missing):
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps({"version": 1, "cases": {CASE: ["synthetic/"]}}), encoding="utf-8")
    args = [mode]
    if mode == "--verify":
        args += [tmp_path / "missing-judgments.json"]
    args += ["--scope-file", scope] if missing == "case" else ["--case-id", CASE]
    completed = cli(*args)
    assert completed.returncode == 2
    message = json.loads(completed.stdout)["error"]
    assert "--case-id" in message and "--scope-file" in message


def test_dry_run_requires_scope_and_case_before_input_reads():
    completed = cli("--dry-run")
    assert completed.returncode == 2
    message = json.loads(completed.stdout)["error"]
    assert "--case-id" in message and "--scope-file" in message


@pytest.mark.parametrize("mode", ["--dry-run", "--select-only", "--verify"])
def test_case_id_comes_from_scope_file_before_input_reads(tmp_path, mode):
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps({"version": 1, "cases": {CASE: ["synthetic/"]}}), encoding="utf-8")
    args = [mode]
    if mode == "--verify":
        args += [tmp_path / "missing-judgments.json"]
    completed = cli(*args, "--case-id", "unlisted-example", "--scope-file", scope)
    assert completed.returncode == 2
    message = json.loads(completed.stdout)["error"]
    assert "--case-id" in message and "cases" in message and "--scope-file" in message
