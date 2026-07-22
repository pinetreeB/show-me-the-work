from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import cast

import pytest

from core.destructive_guard import (
    evaluate_r2_destructive_gate,
    parse_destructive_commands,
)
from core.ledger import JsonObject


ROOT = Path(__file__).resolve().parents[1]

BLOCK_CORPUS = (
    "echo ok && rm peer.py",
    "echo ok ; rm peer.py",
    "echo ok | rm peer.py",
    "echo ok & rm peer.py",
    "echo ok\nrm peer.py",
    "command rm peer.py",
    "exec rm peer.py",
    "{ rm peer.py; }",
    "if true; then rm peer.py; fi",
    "for x in 1; do rm peer.py; done",
    'bash -c "echo ok; rm peer.py"',
    "git checkout -f main",
    "git checkout --force main",
    "git checkout -qf main",
    "git checkout -fB newbranch",
    "git checkout -Bf newbranch",
)

ALLOW_CORPUS = (
    'echo "rm peer.py"',
    'python -c "print(\'rm peer.py\')"',
    "git checkout main",
    "git checkout -b feature/x",
    "git checkout -B feature/x",
    "git checkout --no-force main",
    "tee -a log.txt",
    "tee --append log.txt",
    "tee",
)


def _payload(root: Path, command: str) -> JsonObject:
    return {
        "project_root": str(root),
        "tool_name": "Bash",
        "command": command,
        "host": "codex_cli",
        "session_id": "v261-pr-a",
        "agent": "codex",
    }


def _healthy(_ledger: JsonObject) -> JsonObject:
    return {"degraded": False, "capacity_exceeded": False}


def _peer_record() -> JsonObject:
    return {
        "generation": 1,
        "status": "exclusive",
        "owners": [
            {
                "agent_key": "claude_code:peer-session:claude",
                "settled": False,
            }
        ],
    }


def _lookup_peer(_ledger: JsonObject, canonical_path: str) -> JsonObject | None:
    return _peer_record() if canonical_path in {"peer.py", "file.txt", "-f"} else None


def _decision(root: Path, command: str) -> JsonObject:
    return evaluate_r2_destructive_gate(
        _payload(root, command),
        lookup_path_attribution=_lookup_peer,
        attribution_health=_healthy,
    )


@pytest.mark.parametrize("command", BLOCK_CORPUS)
def test_handoff_13_1_block_corpus_is_denied(tmp_path: Path, command: str) -> None:
    assert _decision(tmp_path, command)["decision"] == "block", command


@pytest.mark.parametrize("command", ALLOW_CORPUS)
def test_handoff_13_2_allow_corpus_remains_allowed(
    tmp_path: Path, command: str
) -> None:
    assert _decision(tmp_path, command)["decision"] == "allow", command


@pytest.mark.parametrize(
    "command",
    (
        "MODE=test rm peer.py",
        "command -p rm peer.py",
        "exec rm peer.py",
        "exec -a worker rm peer.py",
        "sudo -u root rm peer.py",
        "sudo --user root rm peer.py",
        "doas -u root rm peer.py",
        "env MODE=test rm peer.py",
        'env -S "rm peer.py"',
        'env --split-string "rm peer.py"',
        "nohup rm peer.py",
        "nice -n 5 rm peer.py",
        "ionice -c 2 rm peer.py",
        "stdbuf -o 0 rm peer.py",
        "timeout 5 rm peer.py",
        "setsid rm peer.py",
    ),
)
def test_command_position_prefixes_reach_the_real_command(
    tmp_path: Path, command: str
) -> None:
    assert _decision(tmp_path, command)["decision"] == "block", command


@pytest.mark.parametrize(
    "command",
    (
        "if false; then echo no; else rm peer.py; fi",
        "if false; then echo no; elif rm peer.py; then echo no; fi",
        "{ rm peer.py; }",
        "( rm peer.py )",
        "! rm peer.py",
        "for x in 1; do rm peer.py; done",
    ),
)
def test_control_words_open_a_new_command_position(
    tmp_path: Path, command: str
) -> None:
    assert _decision(tmp_path, command)["decision"] == "block", command


@pytest.mark.parametrize(
    "command",
    (
        'bash -c "echo ok; rm peer.py"',
        'sh -c "rm peer.py"',
        'pwsh -Command "Remove-Item peer.py"',
        'cmd /c "del peer.py"',
    ),
)
def test_supported_nested_shells_remain_fail_closed(
    tmp_path: Path, command: str
) -> None:
    assert _decision(tmp_path, command)["decision"] == "block", command


@pytest.mark.parametrize(
    "command",
    (
        'echo "$(rm peer.py)"',
        'echo "$(echo ok; rm peer.py)"',
    ),
)
def test_command_substitutions_open_nested_command_positions(
    tmp_path: Path, command: str
) -> None:
    assert _decision(tmp_path, command)["decision"] == "block", command


@pytest.mark.parametrize(
    "command",
    (
        'echo "a & rm peer.py"',
        'python -c "print(\'rm peer.py\')"',
        'printf "x\ny"',
        r"echo safe \& rm peer.py",
        'bash -c "printf \'rm peer.py\'"',
        "echo '$(rm peer.py)'",
        "command -v rm",
        "command -V rm",
    ),
)
def test_quoted_escaped_and_query_text_is_not_treated_as_a_command(
    tmp_path: Path, command: str
) -> None:
    assert _decision(tmp_path, command)["decision"] == "allow", command


@pytest.mark.parametrize(
    "command",
    (
        "git checkout -qf main",
        "git checkout -fB newbranch",
        "git checkout -Bf newbranch",
    ),
)
def test_checkout_short_clusters_with_force_are_implicit_scope(
    command: str,
) -> None:
    parsed = parse_destructive_commands(command)

    assert len(parsed) == 1
    assert parsed[0].resolved is False
    assert parsed[0].reason == "implicit_scope"


@pytest.mark.parametrize(
    "command",
    (
        "git checkout -q main",
        "git checkout -b feature/x",
        "git checkout -B feature/x",
        "git checkout -bfeature/x",
        "git checkout -Bfeature/x",
        "git checkout -f --no-force main",
    ),
)
def test_checkout_nonforce_options_remain_allowed(
    tmp_path: Path, command: str
) -> None:
    assert _decision(tmp_path, command)["decision"] == "allow", command


def test_checkout_double_dash_stops_force_option_parsing(tmp_path: Path) -> None:
    parsed = parse_destructive_commands("git checkout -- -f")

    assert len(parsed) == 1
    assert parsed[0].resolved is True
    assert parsed[0].targets == ("-f",)
    assert _decision(tmp_path, "git checkout -- -f")["decision"] == "block"


@pytest.mark.parametrize(
    "command",
    (
        "tee file.txt",
        "echo x | tee file.txt",
        "tee fi",
    ),
)
def test_tee_overwrite_has_a_resolved_output_target(command: str) -> None:
    parsed = parse_destructive_commands(command)

    assert len(parsed) == 1
    assert parsed[0].resolved is True
    expected = "fi" if command == "tee fi" else "file.txt"
    assert parsed[0].targets == (expected,)


@pytest.mark.parametrize("command", ("fi", "done", "}"))
def test_standalone_closing_control_words_are_not_commands(command: str) -> None:
    assert parse_destructive_commands(command) == ()


@pytest.mark.parametrize(
    "command",
    (
        "tee -a file.txt",
        "tee --append file.txt",
        "tee",
        "tee --help",
        "echo x | tee -a file.txt",
        "echo x | tee",
    ),
)
def test_tee_append_and_output_only_forms_are_not_destructive(command: str) -> None:
    assert parse_destructive_commands(command) == ()


@pytest.mark.parametrize(
    ("adapter", "command"),
    (
        ("codex_cli", "echo ok & git checkout -qf main"),
        ("claude_code", "if true; then git checkout -Bf branch; fi"),
    ),
)
def test_real_adapters_deny_new_command_position_bypasses(
    tmp_path: Path, adapter: str, command: str
) -> None:
    payload = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": f"v261-{adapter}",
    }
    process = subprocess.run(
        [sys.executable, str(ROOT / "adapters" / adapter / "pre_tool_use.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert process.returncode == 0, process.stderr
    result = cast(JsonObject, json.loads(process.stdout or "{}"))
    if adapter == "claude_code":
        hook_output = result.get("hookSpecificOutput")
        assert isinstance(hook_output, dict), result
        assert hook_output.get("permissionDecision") == "deny", result
        assert "R2" in str(hook_output.get("permissionDecisionReason", "")), result
    else:
        assert result.get("decision") == "block", result
        assert "R2" in str(result.get("reason", "")), result
