"""Codex / Qwen adapters keep the host's project root when the shell cwd is nested.

The host reports both the project root and the shell cwd.  When the cwd is a
subdirectory of that root (the shell stands in `.smtw` or `src`), the adapters
used to drop the root and treat the cwd as the project, so the state directory
protection no longer recognised `<root>/.smtw/config.json` as a state file.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


ADAPTER_ROOT = Path(__file__).resolve().parents[1] / "adapters"


def _run_adapter(script: Path, args: list[str], payload: dict[str, object]) -> str:
    process = subprocess.run(
        [sys.executable, str(script), *args],
        input=json.dumps(payload, ensure_ascii=False),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return process.stdout + process.stderr


@pytest.mark.parametrize(
    ("script", "args", "tool_name"),
    [
        (ADAPTER_ROOT / "codex_cli" / "pre_tool_use.py", [], "Bash"),
        (ADAPTER_ROOT / "qwen_code" / "qwen_hook.py", ["PreToolUse"], "run_shell_command"),
    ],
)
def test_codex_and_qwen_keep_the_ancestor_root_when_the_cwd_is_the_state_dir(
    tmp_path: Path, script: Path, args: list[str], tool_name: str
) -> None:
    # Given: the host reports the project root and a shell cwd inside `.smtw`.
    root = tmp_path / "project"
    state = root / ".smtw"
    state.mkdir(parents=True)
    config = state / "config.json"
    config.write_text(json.dumps({"schema_version": 1, "supervision": True}), encoding="utf-8")
    payload: dict[str, object] = {
        "cwd": str(state),
        "project_root": str(root),
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"command": f"rm {config.as_posix()}"},
        "tool_use_id": "call_rm",
        "session_id": "nested-cwd-session",
    }

    # When: the real PreTool adapter evaluates the delete of the state file.
    output = _run_adapter(script, args, payload)

    # Then: the root stays the project, so `config.json` is still a state file.
    assert "state_dir_protected" in output, output
    assert config.exists()


@pytest.mark.parametrize("module", ["adapters.codex_cli.common", "adapters.qwen_code.common"])
def test_codex_and_qwen_project_root_accepts_an_ancestor_only_when_related(
    tmp_path: Path, module: str
) -> None:
    project_root = importlib.import_module(module).project_root
    root = tmp_path / "project"
    nested = root / ".smtw"
    nested.mkdir(parents=True)
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()

    assert Path(project_root({"cwd": str(nested), "project_root": str(root)})) == root.resolve()
    assert Path(project_root({"cwd": str(root), "project_root": str(nested)})) == nested.resolve()
    assert Path(project_root({"cwd": str(root), "project_root": str(unrelated)})) == root.resolve()
