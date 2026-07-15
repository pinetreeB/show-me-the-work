from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import cast

from core.ledger import JsonObject, load_ledger


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "adapters" / "antigravity" / "oma_hook.py"


def _payload(workspace: Path, step: int, tool_call: JsonObject | None = None) -> JsonObject:
    payload: JsonObject = {
        "conversationId": "agy-design-e2e",
        "modelName": "gemini-3.1-pro",
        "stepIdx": step,
        "artifactDirectoryPath": str(workspace / ".agy" / "artifacts"),
        "transcriptPath": str(workspace / ".agy" / "transcript.jsonl"),
        "workspacePaths": [str(workspace)],
    }
    if tool_call is not None:
        payload["toolCall"] = tool_call
    return payload


def _run_hook(event: str, payload: JsonObject, workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK), event],
        input=json.dumps(payload, ensure_ascii=False),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=workspace,
    )


def _run_git(workspace: Path, *args: str) -> None:
    _ = subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _initialize_design_project(workspace: Path) -> None:
    _ = (workspace / "design").mkdir()
    _ = (workspace / ".agy").mkdir()
    _ = (workspace / "design" / "gate.config").write_text(
        json.dumps({"enabled": True}), encoding="utf-8"
    )
    _ = (workspace / ".agy" / "transcript.jsonl").write_text(
        json.dumps(
            {"role": "user", "content": "src/App.css UI 화면을 디자인 수정해줘"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _ = (workspace / "README.md").write_text("baseline\n", encoding="utf-8")
    _run_git(workspace, "init")
    _run_git(workspace, "config", "user.email", "test@example.com")
    _run_git(workspace, "config", "user.name", "smtw-test")
    _run_git(workspace, "add", ".")
    _run_git(workspace, "commit", "-m", "baseline")


def _turn(workspace: Path) -> dict[str, object]:
    ledger = load_ledger({"project_root": str(workspace)})
    turns = cast(dict[str, object], ledger["active_turns"])
    return cast(dict[str, object], turns["antigravity:agy-design-e2e:antigravity"])


def test_real_payload_recovers_prompt_and_blocks_design_stop(tmp_path: Path) -> None:
    _initialize_design_project(tmp_path)

    pre = _run_hook("PreInvocation", _payload(tmp_path, 1), tmp_path)

    assert pre.returncode == 0
    assert _turn(tmp_path)["design_required"] is True, (
        "PreInvocation must recover the user prompt from transcriptPath when the live payload "
        "has no prompt or llm_request fields"
    )

    css = tmp_path / "src" / "App.css"
    _ = css.parent.mkdir()
    _ = css.write_text(".hero {\n  color: #ff0000;\n  margin: 16px;\n}\n", encoding="utf-8")
    edit = _payload(
        tmp_path,
        2,
        {"name": "write_to_file", "args": {"path": "src/App.css"}},
    )
    edit["error"] = False
    post = _run_hook("PostToolUse", edit, tmp_path)

    assert post.returncode == 0
    assert _turn(tmp_path)["design_touched"] is True

    verify_call: JsonObject = {
        "name": "run_command",
        "args": {"command": 'python -c "assert True"'},
    }
    verify = _payload(tmp_path, 3, verify_call)
    assert _run_hook("PreToolUse", verify, tmp_path).returncode == 0
    verify["success"] = True
    verify["stdout"] = "verification passed"
    verify["error"] = False
    assert _run_hook("PostToolUse", verify, tmp_path).returncode == 0

    design = subprocess.run(
        [sys.executable, "-m", "fable_lite", "check", "--root", str(tmp_path), "--design"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )
    assert design.returncode == 1

    stop = _run_hook("Stop", _payload(tmp_path, 4), tmp_path)
    result = cast(dict[str, object], json.loads(stop.stdout))

    assert stop.returncode == 0
    assert result["decision"] == "block"
    assert "design/raw-color" in cast(str, result["reason"])


def test_pre_tool_use_bootstraps_turn_when_pre_invocation_is_absent(tmp_path: Path) -> None:
    _initialize_design_project(tmp_path)
    read = _payload(
        tmp_path,
        1,
        {"name": "view_file", "args": {"path": "README.md"}},
    )
    assert _run_hook("PreToolUse", read, tmp_path).returncode == 0
    edit = _payload(
        tmp_path,
        2,
        {"name": "write_to_file", "args": {"path": "src/App.css"}},
    )

    result = _run_hook("PreToolUse", edit, tmp_path)

    assert result.returncode == 0
    assert _turn(tmp_path)["design_required"] is True, (
        "PreToolUse must lazily start and classify a turn because live Antigravity has not "
        "confirmed PreInvocation dispatch"
    )
