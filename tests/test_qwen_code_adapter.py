from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import TypeAlias

from adapters.qwen_code.install import render_hooks
from core.ledger_storage import ledger_path


ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = ROOT / "adapters" / "qwen_code"
HOOK = ADAPTERS / "qwen_hook.py"

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
HookPayload: TypeAlias = dict[str, JsonValue]
HookOutput: TypeAlias = dict[str, JsonValue]


def run_hook(event: str, payload: HookPayload | str) -> tuple[int, HookOutput, str]:
    raw_input = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    process = subprocess.run(
        [sys.executable, str(HOOK), event],
        input=raw_input,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        output = json.loads(process.stdout or "{}")
    except json.JSONDecodeError:
        output = {}
    assert isinstance(output, dict)
    return process.returncode, output, process.stderr


def object_value(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def list_value(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def read_ledger(root: Path) -> dict[str, JsonValue]:
    raw = json.loads(ledger_path(str(root)).read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def qwen_prompt_payload(tmp_path: Path, prompt: str) -> HookPayload:
    return {
        "cwd": str(tmp_path),
        "hook_event_name": "UserPromptSubmit",
        "permission_mode": "yolo",
        "prompt": prompt,
        "session_id": "qwen-session-1",
        "transcript_path": str(tmp_path / "transcript.jsonl"),
    }


def test_qwen_user_prompt_submit_injects_pack_context_and_records_ledger(tmp_path: Path) -> None:
    payload = qwen_prompt_payload(tmp_path, "버그 고쳐줘 안되는데요")

    code, output, _ = run_hook("UserPromptSubmit", payload)

    assert code == 0
    hook_output = object_value(output["hookSpecificOutput"])
    context = hook_output["additionalContext"]
    ledger = read_ledger(tmp_path)
    assert hook_output["hookEventName"] == "UserPromptSubmit"
    assert isinstance(context, str)
    assert "조사 팩" in context
    assert ledger["requires_investigation_compliance"] is True


def test_qwen_pretool_blocks_high_risk_write_with_exit2(tmp_path: Path) -> None:
    # qwen 도구명(write_file)이 어댑터에서 정규명(Write)으로 매핑되어
    # core contract 게이트에 도달해야 한다.
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "tool_name": "write_file",
        "tool_input": {
            "file_path": "migrations/001.sql",
            "content": "DROP TABLE users;\n",
        },
        "tool_use_id": "call_write",
        "session_id": "qwen-session-1",
    }

    code, output, stderr = run_hook("PreToolUse", payload)

    # qwen 차단 규약: exit 2 + stderr 사유(실증됨). stdout deny JSON도 병행.
    assert code == 2
    assert "contract.json" in stderr
    assert output.get("decision") == "deny"
    assert "contract.json" in str(output.get("reason", ""))


def test_qwen_pretool_allows_ordinary_edit(tmp_path: Path) -> None:
    run_hook("UserPromptSubmit", qwen_prompt_payload(tmp_path, "app.py 좀 고쳐줘"))
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "tool_name": "edit",
        "tool_input": {"file_path": "app.py", "old_string": "a", "new_string": "b"},
        "tool_use_id": "call_edit",
        "session_id": "qwen-session-1",
    }

    code, output, _ = run_hook("PreToolUse", payload)

    assert code == 0
    assert output.get("decision") != "deny"


def test_qwen_posttool_records_shell_verification(tmp_path: Path) -> None:
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "PostToolUse",
        "tool_name": "run_shell_command",
        "tool_input": {"command": "python -m pytest tests/"},
        "tool_response": "Exit code: 0\nWall time: 1 seconds\nOutput:\n26 passed\n",
        "tool_use_id": "call_shell",
        "session_id": "qwen-session-1",
    }

    code, output, _ = run_hook("PostToolUse", payload)

    assert code == 0
    ledger = read_ledger(tmp_path)
    verification = object_value(list_value(ledger["verification_results"])[0])
    assert "recorded verification." in str(output["systemMessage"])
    assert verification["success"] is True
    assert verification["evidence"] == "26 passed"


def test_qwen_posttool_records_write_file_change_and_scope_warning(tmp_path: Path) -> None:
    run_hook("UserPromptSubmit", qwen_prompt_payload(tmp_path, "app.py만 수정해줘"))
    _ = (tmp_path / "settings.py").write_text("DEBUG=True\n", encoding="utf-8")
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "PostToolUse",
        "tool_name": "write_file",
        "tool_input": {"file_path": "settings.py", "content": "DEBUG=True\n"},
        "tool_response": "Exit code: 0\nOutput:\nFile written successfully\n",
        "tool_use_id": "call_write",
        "session_id": "qwen-session-1",
    }

    code, output, _ = run_hook("PostToolUse", payload)

    assert code == 0
    ledger = read_ledger(tmp_path)
    assert ledger["changed_files_seen"] == ["settings.py"]
    assert "범위 이탈" in str(output["systemMessage"])


def test_qwen_stop_blocks_unverified_change_with_last_assistant_message(tmp_path: Path) -> None:
    run_hook("UserPromptSubmit", qwen_prompt_payload(tmp_path, "버그 고쳐줘 안되는데요"))
    _ = (tmp_path / "app.py").write_text("FIX=True\n", encoding="utf-8")
    run_hook(
        "PostToolUse",
        {
            "cwd": str(tmp_path),
            "hook_event_name": "PostToolUse",
            "tool_name": "write_file",
            "tool_input": {"file_path": "app.py", "content": "FIX=True\n"},
            "tool_response": "Exit code: 0\nOutput:\nFile written successfully\n",
            "tool_use_id": "call_write",
            "session_id": "qwen-session-1",
        },
    )
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "Stop",
        "last_assistant_message": "원인은 설정입니다.",
        "stop_hook_active": False,
        "session_id": "qwen-session-1",
    }

    code, output, _ = run_hook("Stop", payload)

    # Stop block은 exit 0 + {"decision":"block"} (qwen Stop 규약).
    assert code == 0
    assert output["decision"] == "block"
    assert "조사 팩" in str(output["reason"])


def test_qwen_stop_allows_answer_only_investigation_turn(tmp_path: Path) -> None:
    run_hook("UserPromptSubmit", qwen_prompt_payload(tmp_path, "버그 고쳐줘 안되는데요"))
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "Stop",
        "last_assistant_message": "원인은 설정입니다.",
        "stop_hook_active": False,
        "session_id": "qwen-session-1",
    }

    code, output, _ = run_hook("Stop", payload)

    assert code == 0
    assert output.get("decision") != "block"


def test_qwen_pretool_fail_open_on_malformed_payload() -> None:
    code, output, _ = run_hook("PreToolUse", "{not-json")

    assert code == 0
    assert output.get("decision") == "allow"
    assert str(output.get("systemMessage", "")).startswith("[smtw] fail-open")


def test_qwen_session_events_are_safe_noops(tmp_path: Path) -> None:
    payload: HookPayload = {"cwd": str(tmp_path), "session_id": "qwen-session-1"}

    start_code, start_output, _ = run_hook("SessionStart", payload)
    end_code, end_output, _ = run_hook("SessionEnd", payload)

    assert start_code == 0
    assert end_code == 0
    assert start_output.get("decision") not in {"deny", "block"}
    assert end_output.get("decision") not in {"deny", "block"}


def test_qwen_unknown_event_fails_open() -> None:
    code, output, _ = run_hook("NoSuchEvent", {"cwd": "."})

    assert code == 0
    assert output.get("decision") not in {"deny", "block"}


def _isolated_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update({"HOME": str(home), "USERPROFILE": str(home)})
    env["QWEN_CODE_TRUSTED_FOLDERS_PATH"] = str(home / ".qwen" / "trustedFolders.json")
    return env


def _install(target: Path | None, env: dict[str, str], *, upgrade: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(ADAPTERS / "install.py")]
    if target is not None:
        command += ["--target", str(target)]
    if upgrade:
        command.append("--upgrade")
    return subprocess.run(
        command,
        cwd=str(target) if target is not None else None,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_qwen_installer_user_install_merges_hooks_preserving_settings(tmp_path: Path) -> None:
    home = tmp_path / "격리 home"
    (home / ".qwen").mkdir(parents=True)
    existing = {
        "model": {"name": "qwen3.8-max-preview"},
        "hooks": {"PreToolUse": [{"matcher": "foreign", "hooks": [{"type": "command", "command": "python foreign.py"}]}]},
    }
    (home / ".qwen" / "settings.json").write_text(json.dumps(existing), encoding="utf-8")
    env = _isolated_env(home)

    result = _install(None, env)

    assert result.returncode == 0, result.stderr
    installed = json.loads((home / ".qwen" / "settings.json").read_text(encoding="utf-8"))
    # 기존 설정 키와 외부 훅이 보존된다.
    assert installed["model"] == {"name": "qwen3.8-max-preview"}
    pretool = installed["hooks"]["PreToolUse"]
    entries = [entry for matcher in pretool for entry in matcher.get("hooks", [])]
    commands = [str(entry.get("command", "")) for entry in entries]
    assert any("foreign.py" in command for command in commands)
    # Windows 렌더는 qwen_hook.py 경로가 env.SMTW_HOOK에 담긴다(%ENV% 형식).
    assert any(
        "qwen_hook.py" in str(entry.get("command", "")).replace("\\", "/")
        or "qwen_hook.py" in str(entry.get("env", {}).get("SMTW_HOOK", "")).replace("\\", "/")
        for entry in entries
    )
    assert set(installed["hooks"]) >= {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}


def test_qwen_installer_upgrade_replaces_only_owned_entries(tmp_path: Path) -> None:
    home = tmp_path / "upgrade home"
    (home / ".qwen").mkdir(parents=True)
    foreign_entry = {"type": "command", "command": "python foreign.py", "foreign": True}
    existing = {
        "hooks": {
            "Stop": [
                {"matcher": "foreign", "hooks": [foreign_entry]},
                {"hooks": [{"type": "command", "command": "python C:/old/adapters/qwen_code/qwen_hook.py Stop"}]},
            ]
        }
    }
    (home / ".qwen" / "settings.json").write_text(json.dumps(existing), encoding="utf-8")
    env = _isolated_env(home)

    result = _install(None, env, upgrade=True)

    assert result.returncode == 0, result.stderr
    upgraded = json.loads((home / ".qwen" / "settings.json").read_text(encoding="utf-8"))
    stop_entries = [entry for matcher in upgraded["hooks"]["Stop"] for entry in matcher.get("hooks", [])]
    assert foreign_entry in stop_entries
    owned = [
        entry
        for entry in stop_entries
        if "/adapters/qwen_code/qwen_hook.py" in str(entry.get("command", "")).replace("\\", "/")
        or "/adapters/qwen_code/qwen_hook.py"
        in str(entry.get("env", {}).get("SMTW_HOOK", "")).replace("\\", "/")
    ]
    assert len(owned) == 1
    assert "C:/old/" not in json.dumps(owned[0])


def test_qwen_installer_workspace_install_registers_trust(tmp_path: Path) -> None:
    home = tmp_path / "ws home"
    home.mkdir()
    target = tmp_path / "외부 프로젝트 with spaces"
    target.mkdir()
    env = _isolated_env(home)

    result = _install(target, env)

    assert result.returncode == 0, result.stderr
    settings_path = target / ".qwen" / "settings.json"
    assert settings_path.is_file()
    installed = json.loads(settings_path.read_text(encoding="utf-8"))
    assert set(installed["hooks"]) >= {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
    trusted = json.loads((home / ".qwen" / "trustedFolders.json").read_text(encoding="utf-8"))
    assert trusted.get(str(target.resolve())) == "TRUST_FOLDER"


def test_qwen_installer_refuses_invalid_settings_preserving_bytes(tmp_path: Path) -> None:
    home = tmp_path / "invalid home"
    (home / ".qwen").mkdir(parents=True)
    settings_path = home / ".qwen" / "settings.json"
    original = b"{not-json\r\n"
    settings_path.write_bytes(original)
    env = _isolated_env(home)

    result = _install(None, env, upgrade=True)

    assert result.returncode == 1
    assert settings_path.read_bytes() == original


def test_qwen_render_hooks_command_form() -> None:
    rendered = render_hooks(Path("C:/smtw root" if os.name == "nt" else "/smtw root"))
    assert rendered is not None
    manifest = json.loads(rendered)
    hooks = manifest["hooks"]
    assert set(hooks) >= {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionStart", "SessionEnd"}
    entry = hooks["PreToolUse"][0]["hooks"][0]
    assert entry["timeout"] == 30000
    if os.name == "nt":
        # Windows: command에 embedded 따옴표 금지(실증: spawn 실패), %ENV% + env 따옴표 형식.
        assert '"' not in entry["command"]
        assert "%SMTW_PYTHON%" in entry["command"]
        assert "%SMTW_HOOK%" in entry["command"]
        assert entry["env"]["SMTW_PYTHON"].startswith('"')
        assert "qwen_hook.py" in entry["env"]["SMTW_HOOK"].replace("\\", "/")
    else:
        assert "qwen_hook.py" in entry["command"]
        parts = shlex.split(entry["command"])
        assert parts[-1] == "PreToolUse"


def test_qwen_external_self_located_stop_blocks_unverified_change(tmp_path: Path) -> None:
    # 설치된 훅 명령이 자기 위치(self-locating)로 core 게이트에 도달하는 end-to-end.
    home = tmp_path / "격리 home"
    home.mkdir()
    target = tmp_path / "외부 프로젝트 with spaces"
    target.mkdir()
    env = _isolated_env(home)
    install = _install(target, env)
    assert install.returncode == 0, install.stderr
    manifest = json.loads((target / ".qwen" / "settings.json").read_text(encoding="utf-8"))
    hooks = manifest["hooks"]

    def run_installed(event: str, payload: HookPayload) -> tuple[int, HookOutput, str]:
        entry = hooks[event][0]["hooks"][0]
        command = str(entry["command"])
        if os.name == "nt":
            # qwen이 실행하는 그대로: cmd /d /s /c + env 확장.
            args: str | list[str] = ["cmd", "/d", "/s", "/c", command]
        else:
            args = shlex.split(command)
        process = subprocess.run(
            args,
            cwd=str(target),
            env={**env, **{key: str(value) for key, value in entry.get("env", {}).items()}},
            input=json.dumps(payload, ensure_ascii=False),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            output = json.loads(process.stdout or "{}")
        except json.JSONDecodeError:
            output = {}
        assert isinstance(output, dict)
        return process.returncode, output, process.stderr

    code, _, stderr = run_installed(
        "UserPromptSubmit",
        {
            "cwd": str(target),
            "prompt": "app.py에 계산 페이지를 구현하고 회귀 테스트까지 실행해줘",
            "session_id": "external-qwen",
        },
    )
    assert code == 0, stderr
    _ = (target / "app.py").write_text("FIX=True\n", encoding="utf-8")
    code, _, stderr = run_installed(
        "PostToolUse",
        {
            "cwd": str(target),
            "tool_name": "write_file",
            "tool_input": {"file_path": "app.py", "content": "FIX=True\n"},
            "tool_response": "Exit code: 0\nOutput:\nFile written successfully",
            "session_id": "external-qwen",
        },
    )
    assert code == 0, stderr

    code, output, stderr = run_installed(
        "Stop",
        {
            "cwd": str(target),
            "last_assistant_message": "변경을 완료했습니다.",
            "stop_hook_active": False,
            "session_id": "external-qwen",
        },
    )

    assert code == 0, stderr
    ledger = json.loads(ledger_path(str(target)).read_text(encoding="utf-8"))
    assert isinstance(ledger, dict)
    assert ledger["task_mode"] == "normal"
    assert output["decision"] == "block"
