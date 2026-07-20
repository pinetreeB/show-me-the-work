from __future__ import annotations

from pathlib import Path

import pytest

from core.destructive_guard import evaluate_r2_destructive_gate
from core.ledger import JsonObject


def _payload(tmp_path: Path, command: str) -> JsonObject:
    return {
        "project_root": str(tmp_path),
        "tool_name": "Bash",
        "command": command,
        "host": "codex_cli",
        "agent": "codex",
        "session_id": "r2-force-checkout",
    }


@pytest.mark.parametrize(
    "command",
    [
        "git checkout -f main",
        "git checkout --force main",
        "git checkout -f release/v2",
    ],
)
def test_r2_blocks_forced_checkout_branch_switch(
    tmp_path: Path,
    command: str,
) -> None:
    result = evaluate_r2_destructive_gate(_payload(tmp_path, command))

    assert result["decision"] == "block"
    assert "implicit_scope" in str(result["reason"])


@pytest.mark.parametrize(
    "command",
    [
        "git checkout main",
        "git checkout release/v2",
        "git checkout --no-force main",
    ],
)
def test_r2_still_allows_nonforced_branch_switch(
    tmp_path: Path,
    command: str,
) -> None:
    result = evaluate_r2_destructive_gate(_payload(tmp_path, command))

    assert result["decision"] == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "git checkout -b feature/r2-safe",
        "git checkout -B feature/r2-safe",
    ],
)
def test_r2_still_allows_checkout_branch_creation(
    tmp_path: Path,
    command: str,
) -> None:
    result = evaluate_r2_destructive_gate(_payload(tmp_path, command))

    assert result["decision"] == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "git checkout -f -b feature/r2-discard",
        "git checkout -B feature/r2-discard --force",
    ],
)
def test_r2_force_takes_precedence_over_branch_creation(
    tmp_path: Path,
    command: str,
) -> None:
    result = evaluate_r2_destructive_gate(_payload(tmp_path, command))

    assert result["decision"] == "block"
    assert "implicit_scope" in str(result["reason"])
