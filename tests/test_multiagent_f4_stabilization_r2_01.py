from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import cast

import pytest

import core.destructive_guard as guard
from core.destructive_guard import evaluate_r2_destructive_gate
from core.ledger import JsonObject


ROOT = Path(__file__).resolve().parents[1]


def _payload(tmp_path: Path, command: str, *, tool_name: str = "Bash") -> JsonObject:
    return {
        "project_root": str(tmp_path),
        "tool_name": tool_name,
        "command": command,
        "host": "codex_cli",
        "agent": "codex",
        "session_id": "r2-chain",
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


def test_r2_inspects_destructive_command_after_benign_segment(
    tmp_path: Path,
) -> None:
    looked_up: list[str] = []

    def lookup(_ledger: JsonObject, canonical_path: str) -> JsonObject | None:
        looked_up.append(canonical_path)
        return _peer_record() if canonical_path == "peer-owned.py" else None

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "echo ok && rm peer-owned.py"),
        lookup_path_attribution=lookup,
        attribution_health=_healthy,
    )

    assert result["decision"] == "block"
    assert looked_up == ["peer-owned.py"]


def test_r2_inspects_every_destructive_segment(tmp_path: Path) -> None:
    looked_up: list[str] = []

    def lookup(_ledger: JsonObject, canonical_path: str) -> JsonObject | None:
        looked_up.append(canonical_path)
        if canonical_path == "own.py":
            return {
                "generation": 1,
                "status": "exclusive",
                "owners": [
                    {
                        "agent_key": "codex_cli:r2-chain:codex",
                        "settled": False,
                    }
                ],
            }
        return _peer_record() if canonical_path == "peer-owned.py" else None

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm own.py && rm peer-owned.py"),
        lookup_path_attribution=lookup,
        attribution_health=_healthy,
    )

    assert result["decision"] == "block"
    assert looked_up == ["own.py", "peer-owned.py"]


def test_r2_parser_returns_every_destructive_segment() -> None:
    parser = getattr(guard, "parse_destructive_commands", None)

    assert parser is not None
    parsed = parser("rm own.py && git restore peer-owned.py")
    assert [item.targets for item in parsed] == [("own.py",), ("peer-owned.py",)]


def test_r2_does_not_split_operators_inside_quotes(tmp_path: Path) -> None:
    command = 'python -c "print(\'a && rm x; b | c || d\')"'

    result = evaluate_r2_destructive_gate(_payload(tmp_path, command))

    assert result["decision"] == "allow"


@pytest.mark.parametrize(
    ("tool_name", "command"),
    [
        ("Bash", "echo ok ; rm peer-owned.py"),
        ("Bash", "printf ok | sed s/o/a/ && rm peer-owned.py"),
        (
            "PowerShell",
            "Write-Output ok | Write-Output; Remove-Item peer-owned.py",
        ),
    ],
)
def test_r2_handles_semicolon_and_pipeline_boundaries(
    tmp_path: Path,
    tool_name: str,
    command: str,
) -> None:
    def lookup(_ledger: JsonObject, canonical_path: str) -> JsonObject | None:
        return _peer_record() if canonical_path == "peer-owned.py" else None

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, command, tool_name=tool_name),
        lookup_path_attribution=lookup,
        attribution_health=_healthy,
    )

    assert result["decision"] == "block"


def test_r2_parse_unable_later_segment_fails_closed(tmp_path: Path) -> None:
    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "echo ok && git reset --hard HEAD")
    )

    assert result["decision"] == "block"
    assert "implicit_scope" in str(result["reason"])


@pytest.mark.parametrize(
    ("tool_name", "command"),
    [
        ("Bash", r"echo safe \; rm peer-owned.py"),
        ("Bash", r"echo safe \| rm peer-owned.py"),
        ("PowerShell", 'Write-Output "safe; Remove-Item peer-owned.py"'),
    ],
)
def test_r2_preserves_escaped_and_powershell_quoted_boundaries(
    tmp_path: Path,
    tool_name: str,
    command: str,
) -> None:
    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, command, tool_name=tool_name)
    )

    assert result["decision"] == "allow"


def test_codex_adapter_blocks_destructive_later_segment(tmp_path: Path) -> None:
    payload: JsonObject = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "echo ok && git reset --hard HEAD"},
    }
    process = subprocess.run(
        [sys.executable, str(ROOT / "adapters" / "codex_cli" / "pre_tool_use.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert process.returncode == 0, process.stderr
    result = cast(JsonObject, json.loads(process.stdout or "{}"))
    assert result.get("decision") == "block", result
    assert "R2" in str(result.get("reason", "")), result
