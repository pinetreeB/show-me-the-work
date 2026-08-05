from __future__ import annotations

from pathlib import Path

import pytest

from core.destructive_guard import (
    evaluate_r2_destructive_gate,
    parse_destructive_command,
)
from core.ledger import JsonObject


def _healthy(_ledger: JsonObject) -> JsonObject:
    return {"degraded": False, "capacity_exceeded": False}


def _decision(root: Path, command: str, peer_path: str | None) -> str:
    def lookup(_ledger: JsonObject, canonical: str) -> JsonObject | None:
        if canonical != peer_path:
            return None
        return {
            "generation": 1,
            "status": "exclusive",
            "owners": [{"agent_key": "host:peer:codex", "settled": False}],
        }

    result = evaluate_r2_destructive_gate(
        {
            "project_root": str(root),
            "tool_name": "Bash",
            "command": command,
            "host": "host",
            "session_id": "pr54-p2",
            "agent": "codex",
            "attribution": "exact",
        },
        lookup_path_attribution=lookup,
        attribution_health=_healthy,
    )
    return str(result["decision"])


@pytest.mark.parametrize(
    ("command", "peer_path"),
    (
        ("Remove-Item -Path:'peer dir/file.py'", "peer dir/file.py"),
        ('Remove-Item -Path:"peer dir/file.py"', "peer dir/file.py"),
        ("Copy-Item -Destination:'peer dir/file.py' mine.py", "peer dir/file.py"),
        ("git restore -s HEAD -p", None),
        ("git checkout --detach HEAD -p", None),
        ("git checkout -pq", None),
        ("git restore -pq", None),
    ),
)
def test_pr54_p2_destructive_forms_reach_gate(
    tmp_path: Path,
    command: str,
    peer_path: str | None,
) -> None:
    assert _decision(tmp_path, command, peer_path) == "block"


@pytest.mark.parametrize(
    "command",
    (
        "Remove-Item -Path:'peer dir/file.py'",
        'Remove-Item -Path:"peer dir/file.py"',
        "Copy-Item -Destination:'peer dir/file.py' mine.py",
    ),
)
def test_colon_attached_quoted_value_preserves_complete_path(command: str) -> None:
    parsed = parse_destructive_command(command)

    assert parsed is not None
    assert parsed.resolved is True
    assert "peer dir/file.py" in parsed.targets


def test_unknown_powershell_parameter_still_advances_to_path() -> None:
    parsed = parse_destructive_command("Remove-Item -Bogus peer.py")

    assert parsed is not None
    assert parsed.resolved is True
    assert "peer.py" in parsed.targets


@pytest.mark.parametrize(
    "command",
    (
        "git restore -s HEAD -p",
        "git checkout --detach HEAD -p",
        "git checkout -pq",
        "git restore -pq",
    ),
)
def test_pathless_git_patch_after_options_is_implicit_scope(command: str) -> None:
    parsed = parse_destructive_command(command)

    assert parsed is not None
    assert parsed.resolved is False
    assert parsed.reason == "implicit_scope"


@pytest.mark.parametrize(
    ("command", "peer_path"),
    (
        ("Remove-Item -Path:'my own/file.py'", "peer dir/file.py"),
        ("Get-Content -Path:'peer dir/file.py'", "peer dir/file.py"),
        ("git checkout --detach HEAD", None),
        ("git restore -s HEAD my_own.py", "peer.py"),
    ),
)
def test_pr54_p2_non_destructive_controls_stay_allowed(
    tmp_path: Path,
    command: str,
    peer_path: str | None,
) -> None:
    assert _decision(tmp_path, command, peer_path) == "allow"


@pytest.mark.parametrize(
    ("command", "peer_path"),
    (
        ("Move-Item -Path peer.py out", "peer.py"),
        ("Rename-Item -Path peer.py new.py", "peer.py"),
        ("Move-Item -Path:peer.py out", "peer.py"),
        ("mi -Path peer.py out", "peer.py"),
        ("Move-Item -LiteralPath peer.py out", "peer.py"),
        ("Move-Item -Path:'peer dir/file.py' out", "peer dir/file.py"),
        ("Move-Item -Path 'peer dir/file.py' out", "peer dir/file.py"),
        ("Rename-Item -Path 'peer dir/file.py' new.py", "peer dir/file.py"),
        ("Copy-Item -Path mine.py peer.py", "peer.py"),
    ),
)
def test_named_source_with_positional_destination_reaches_gate(
    tmp_path: Path,
    command: str,
    peer_path: str,
) -> None:
    assert _decision(tmp_path, command, peer_path) == "block"


@pytest.mark.parametrize(
    ("command", "expected_targets"),
    (
        ("Move-Item -Path peer.py out", ("peer.py", "out", "out/peer.py")),
        ("Rename-Item -Path peer.py new.py", ("peer.py", "new.py")),
        ("Copy-Item -Path mine.py peer.py", ("peer.py", "peer.py/mine.py")),
        (
            "Move-Item -Path:'peer dir/file.py' out",
            ("peer dir/file.py", "out", "out/file.py"),
        ),
    ),
)
def test_named_source_with_positional_destination_preserves_targets(
    command: str,
    expected_targets: tuple[str, ...],
) -> None:
    parsed = parse_destructive_command(command)

    assert parsed is not None
    assert parsed.resolved is True
    assert parsed.targets == expected_targets


@pytest.mark.parametrize(
    ("command", "peer_path"),
    (
        ("Move-Item -Path my_own.py out", "peer.py"),
        ("Rename-Item -Path my_own.py new.py", "peer.py"),
        ("Copy-Item -Path peer.py out", "peer.py"),
    ),
)
def test_named_source_with_positional_destination_controls_stay_allowed(
    tmp_path: Path,
    command: str,
    peer_path: str,
) -> None:
    assert _decision(tmp_path, command, peer_path) == "allow"
