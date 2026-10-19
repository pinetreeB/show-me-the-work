"""Synthetic work-document scope, audit trail and offline CLI acceptance."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from smtw.claims import judge
from smtw.claims.advisory import load_sent_items
from smtw.claims.judge_scope import load_scope, work_source_basis, directly_referenced
from smtw.claims.packets import hard_excluded, sent_sources
from test_claims_assist_scope import cli


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("REAL MODEL LAUNCH FORBIDDEN")
    monkeypatch.setattr(judge, "_launch", forbidden)


def scope_file(tmp_path, roots=None, checkpoint=None):
    path = tmp_path / "scope.json"
    path.write_text(json.dumps({"version": 1, "cases": {"work-example": {
        "source_prefixes": ["synthetic/repo/"],
        "work_roots": roots or ["C:/synthetic/tasks/example"],
        "checkpoint": checkpoint or "C:/synthetic/checkpoints/example.md"}}}), encoding="utf-8")
    return path


@pytest.mark.parametrize("path", [
    "nested/_customer-data-DO-NOT-READ/doc.md", "CUSTOMER-DATA/a.md",
    "prefix.secrets-suffix/a.md", ".env.local", "backup.env.copy",
    "keys/a.md", "token.json", "cert.pem", "pre-tax-post/a.md",
    "세무원본/a.md", "pre-legal-post/a.md", "법무원본/a.md",
    "pre-contract-post/a.md", "계약원본/a.md",
    "memory/core/user_personal_sensitive.md", "x/../customer-data/a.md",
    "%2eenv.local", "x/%2e%2e/.secrets/a.md",
])
def test_hard_exclude_normalized_substrings(path):
    assert hard_excluded("C:/synthetic/" + path)


def test_work_scope_requires_exact_reference_and_root(tmp_path):
    scope = load_scope(scope_file(tmp_path))
    target = "C:/synthetic/tasks/example/card.md"
    assert work_source_basis("work-example", target, scope, report="card.md:8") == "report_reference"
    assert work_source_basis("work-example", target, scope, report=target) == "report_reference"
    assert not work_source_basis("work-example", target, scope, report="other-card.md")
    assert not work_source_basis("work-example", target, scope, report="card.md.backup")
    assert not work_source_basis("work-example", target.replace("/example/", "/example-other/"), scope, report="card.md")
    assert not work_source_basis("work-example", target.replace("card.md", "../outside.md"), scope, report="outside.md")
    assert work_source_basis("work-example", scope.checkpoints["work-example"], scope, report="") == "checkpoint"
    assert not directly_referenced("another/card.md", target)


@pytest.mark.parametrize("roots,checkpoint", [
    (["relative/task"], "C:/synthetic/state.md"),
    (["C:/synthetic/task"], ["C:/synthetic/a.md", "C:/synthetic/b.md"]),
])
def test_invalid_work_scope_rejected(tmp_path, roots, checkpoint):
    with pytest.raises(ValueError):
        load_scope(scope_file(tmp_path, roots, checkpoint))


@pytest.mark.parametrize("kind", ["agy", "codex"])
def test_work_cli_excerpt_and_judge_preflight_without_model(tmp_path, kind):
    work = "C:/synthetic/tasks/example"
    checkpoint = "C:/synthetic/checkpoints/example.md"
    scope = scope_file(tmp_path, [work], checkpoint)
    blocklist = tmp_path / "synthetic-blocklist.txt"
    blocklist.write_text("Synthetic Blocked Example\n", encoding="utf-8")
    report = tmp_path / "report.md"
    report.write_text("card.md:1 WorkStatus 설명.\nWorkStatus returns 7 ms.\n", encoding="utf-8")
    # These source files deliberately do not exist: only captured read output is allowed.
    files = {work + "/card.md": "WorkStatus returns seven milliseconds.\n" + "Context line details.\n" * 200,
             work + "/unmentioned.md": "WorkStatus UNMENTIONED_MARKER\n",
             work + "/pre-tax-post/a.md": "WorkStatus HARD_EXCLUDE_MARKER\n",
             work + "/.env.extra": "WorkStatus HARD_EXCLUDE_MARKER\n",
             checkpoint: "WorkStatus CHECKPOINT_MARKER confirmed.\n" + "State history.\n" * 200}
    rows = []
    for index, (path, content) in enumerate(files.items()):
        if kind == "agy":
            lines = content.splitlines()
            rows += [{"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "view_file", "args": {"AbsolutePath": path}}]},
                {"type": "GENERIC", "status": "DONE",
                 "content": f"File Path: {path}\nTotal Lines: {len(lines)}\nShowing lines 1 to {len(lines)}\n"
                 + "\n".join(f"{i}: {line}" for i, line in enumerate(lines, 1))}]
        else:
            ident = "work-read-" + str(index)
            rows += [{"type": "response_item", "payload": {
                "type": "function_call", "name": "exec_command", "call_id": ident,
                "arguments": json.dumps({"cmd": "Get-Content -LiteralPath " + json.dumps(path)})}},
                {"type": "response_item", "payload": {
                    "type": "function_call_output", "call_id": ident,
                    "output": json.dumps({"output": content, "exit_code": 0})}}]
    transcript = tmp_path / (kind + ".jsonl")
    transcript.write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    out = tmp_path / "packets"
    result = cli("--report", report, "--" + kind, transcript, "--dry-run", "--out", out,
                 "--case-id", "work-example", "--scope-file", scope, "--blocklist-file", blocklist, "--format", "json")
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads(result.stdout)
    sent = load_sent_items(out)
    assert sent and manifest["skipped_hard_exclude"] == (2 if kind == "agy" else 1)
    excerpts = [e for item in sent.values() for e in item["excerpts"]]
    assert {e["path"] for e in excerpts} == {work + "/card.md", checkpoint}
    assert all(len(e["text"]) <= 1500 and len(e["text"].splitlines()) < 201 for e in excerpts)
    assert all(len(item["excerpts"]) <= 3 for item in sent.values())
    assert all("UNMENTIONED_MARKER" not in e["text"] and "HARD_EXCLUDE_MARKER" not in e["text"] for e in excerpts)
    assert manifest["sent_sources"] == sent_sources(sent)
    judge.validate_scope(manifest, sent, load_scope(scope))
    launches = []
    def fake(argv, prompt, env):
        launches.append(prompt)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"subtype": "success", "is_error": False, "structured_output": {"judgments": [
            {"id": key, "verdict": "not_enough_evidence", "rationale": "Synthetic preflight test."}
            for key in sent]}}))
    output = tmp_path / "judge-output"
    result = judge.judge(out, scope_file=scope, output_root=output, executor=fake, tty=lambda: True,
                         parent_env={}, executable="claude-p", blocklist_file=blocklist,
                         usage_reader=lambda: {"known": True, "five_hour": 1, "weekly": 2})
    assert result["execution"] == "injected" and len(launches) == 1
    record = json.loads((output / "calls.jsonl").read_text(encoding="utf-8"))
    assert record["sent_sources"] == manifest["sent_sources"]
    for item in sent.values():
        for excerpt in item["excerpts"]:
            excerpt["source"] = "fixed_commit"
    with pytest.raises(ValueError, match="outside approved"):
        judge.validate_scope(manifest, sent, load_scope(scope))
