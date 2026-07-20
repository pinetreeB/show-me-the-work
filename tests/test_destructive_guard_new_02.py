from __future__ import annotations

from pathlib import Path

import pytest

from core.adapter_observation import CanonicalInvocation, record_r2_deny_after_resolution
from core.destructive_guard import (
    R2_COORDINATION_REASON_MAP,
    evaluate_r2_destructive_gate,
)
from core.ledger import load_ledger
from core.scorecard_coordination import parse_coordination_event


PARSE_UNABLE_REASONS = frozenset(
    {
        "parse_unable_dynamic_command",
        "parse_unable_dynamic_expression",
        "parse_unable_missing_path_flag",
        "parse_unable_missing_target",
        "parse_unable_missing_value",
        "parse_unable_obfuscated",
        "parse_unable_pathspec_from_file",
        "parse_unable_pipeline",
        "parse_unable_subcommand",
        "parse_unable_target",
        "parse_unable_wrapped",
    }
)


def _payload(root: Path, command: str) -> dict[str, str]:
    return {
        "project_root": str(root),
        "tool_name": "PowerShell",
        "command": command,
        "host": "claude_code",
        "session_id": "new-02-session",
        "agent": "claude",
    }


def test_all_parser_failures_have_an_explicit_coordination_label() -> None:
    assert PARSE_UNABLE_REASONS - R2_COORDINATION_REASON_MAP.keys() == set()
    assert {
        R2_COORDINATION_REASON_MAP[reason].value for reason in PARSE_UNABLE_REASONS
    } == {"command_parse_unavailable"}


@pytest.mark.parametrize(
    ("command", "raw_reason", "message_fragment"),
    [
        (
            "$content = 'report'; "
            "Set-Content -Path docs/reviews/report.md -Value $content",
            "parse_unable_dynamic_command",
            "동적 명령",
        ),
        (
            "Set-Content -Path (Join-Path $PWD 'docs/reviews/report.md') "
            "-Value report",
            "parse_unable_dynamic_expression",
            "동적 경로 표현식",
        ),
    ],
)
def test_dynamic_parser_denial_uses_accurate_reason_and_message(
    tmp_path: Path,
    command: str,
    raw_reason: str,
    message_fragment: str,
) -> None:
    result = evaluate_r2_destructive_gate(_payload(tmp_path, command))

    assert result["decision"] == "block"
    assert result["coordination_reason_code"] == "command_parse_unavailable"
    assert raw_reason in str(result["reason"])
    assert message_fragment in str(result["reason"])
    assert "정적으로 해석" in str(result["reason"])
    assert "fail-closed" in str(result["reason"])


def test_dynamic_parser_deny_can_be_recorded_with_its_own_reason(
    tmp_path: Path,
) -> None:
    command = "$content = 'report'; Set-Content docs/reviews/report.md $content"
    decision = evaluate_r2_destructive_gate(_payload(tmp_path, command))
    invocation = CanonicalInvocation(
        "claude_code",
        "claude",
        "new-02-session",
        "new-02-turn",
        "new-02-invocation",
        "pre_tool",
        "shell",
        (),
        command,
        False,
        "",
    )

    recorded = record_r2_deny_after_resolution(
        tmp_path,
        invocation,
        str(decision["coordination_reason_code"]),
    )

    assert recorded is True
    outbox = load_ledger({"project_root": str(tmp_path)})["coordination_outbox"]
    assert isinstance(outbox, dict) and len(outbox) == 1
    event = parse_coordination_event(next(iter(outbox.values())))
    assert event.reason_code.value == "command_parse_unavailable"
