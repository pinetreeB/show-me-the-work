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


def _decision(root: Path, command: str, peer_path: str | None = "peer.py") -> str:
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
            "session_id": "review2-fix41",
            "agent": "codex",
            "attribution": "exact",
        },
        lookup_path_attribution=lookup,
        attribution_health=_healthy,
    )
    return str(result["decision"])


@pytest.mark.parametrize(
    "command",
    (
        "sed -ni s/x/y/ peer.py",
        "sed -Ei s/x/y/ peer.py",
        "find . -fprint peer.py",
        "find . -fprint0 peer.py",
        "find . -fls peer.py",
        "find . -fprintf peer.py '%p\\n'",
        "Copy-Item -Path:mine.py -Destination:peer.py",
        "Move-Item -Path:peer.py -Destination:moved.py",
        "Copy-Item -Path mine.py -Dest peer.py",
        "Rename-Item -Path peer.py -New renamed.py",
        "New-Item -Fo peer.py",
        "'mine.py' | Copy-Item -Destination peer.py",
        "'Remove-Item peer.py' | Invoke-Expression",
        "'x' | Tee-Object -FilePath peer.py",
        "'x' | Tee-Object -LiteralPath peer.py",
        "'x' | Tee-Object peer.py",
        "git switch -f main",
        "git switch --force main",
        "git switch --orphan newroot",
        "git checkout -p",
        "git restore --patch",
        "cp -tpeer-dir mine.py",
        "cp --context mine.py peer.py",
        "Remove-Item (gi peer.py)",
        "ri (Join-Path a peer.py)",
        "del (gi peer.py)",
        "Remove-Item @(peer.py)",
        "Clear-Content -Path @(peer.py)",
        "Move-Item @(peer.py) out",
        "New-Item -Force @(peer.py)",
        "echo x | nice bash",
        "echo x | setsid bash",
        "echo x | ionice -c 2 bash",
        "echo x | stdbuf -oL bash",
        "echo x | time bash",
        "echo x | busybox sh",
        "echo x | toybox bash",
        "echo x | powershell",
        "echo x | pwsh",
        "echo x | cmd",
    ),
)
def test_documented_destructive_syntax_reaches_gate(
    tmp_path: Path,
    command: str,
) -> None:
    peer_path = "peer-dir/mine.py" if command == "cp -tpeer-dir mine.py" else "peer.py"

    assert _decision(tmp_path, command, peer_path) == "block"


@pytest.mark.parametrize(
    "command",
    (
        "perl -Ilib -e s/x/y/ peer.py",
        "perl -Mstrict -e s/x/y/ peer.py",
        "sc stop peersvc",
        "sc query peersvc",
        "Remove-Item peer.py -WhatIf:$true",
        "'x' | Tee-Object -Append peer.py",
        "cat README.md | head -20",
    ),
)
def test_documented_non_destructive_syntax_stays_allowed(
    tmp_path: Path,
    command: str,
) -> None:
    assert _decision(tmp_path, command) == "allow"


def test_false_whatif_and_ambiguous_parameter_prefix_fail_closed(
    tmp_path: Path,
) -> None:
    assert _decision(tmp_path, "Remove-Item peer.py -WhatIf:$false") == "block"
    assert (
        _decision(tmp_path, "Copy-Item -Pa mine.py -Destination out", None) == "block"
    )


@pytest.mark.parametrize(
    ("command", "targets"),
    (
        ("cp -tshared-dir peer.py", ("shared-dir", "shared-dir/peer.py")),
        (
            "cp --target-directory=shared-dir peer.py",
            ("shared-dir", "shared-dir/peer.py"),
        ),
        (
            "mv -tshared-dir peer.py",
            ("peer.py", "shared-dir", "shared-dir/peer.py"),
        ),
    ),
)
def test_attached_target_directory_routes_derived_write_candidate(
    command: str,
    targets: tuple[str, ...],
) -> None:
    parsed = parse_destructive_command(command)

    assert parsed is not None
    assert parsed.resolved is True
    assert parsed.targets == targets


def test_remove_expression_matches_other_destructive_detector() -> None:
    move = parse_destructive_command("mv (gi peer.py) out")
    remove = parse_destructive_command("Remove-Item (gi peer.py)")

    assert move is not None and remove is not None
    assert move.resolved is False
    assert remove.resolved is False
    assert move.reason == remove.reason == "parse_unable_dynamic_expression"


@pytest.mark.parametrize("literal", ("'(literal).py'", "'@(literal).py'"))
def test_quoted_expression_shaped_path_remains_static(literal: str) -> None:
    parsed = parse_destructive_command(f"Remove-Item {literal}")

    assert parsed is not None
    assert parsed.resolved is True
