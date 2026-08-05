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
    def lookup(_ledger: JsonObject, canonical_path: str) -> JsonObject | None:
        if peer_path is None or canonical_path != peer_path:
            return None
        return {
            "generation": 1,
            "status": "exclusive",
            "owners": [
                {
                    "agent_key": "codex_cli:peer-session:codex",
                    "settled": False,
                }
            ],
        }

    result = evaluate_r2_destructive_gate(
        {
            "project_root": str(root),
            "tool_name": "Bash",
            "command": command,
            "host": "codex_cli",
            "session_id": "hardreview-r2-prreview",
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
        "Clear-Content -Path (Join-Path . peer.py)",
        "Clear-Content -LiteralPath (Join-Path . peer.py)",
        "Copy-Item mine.py -Destination (Join-Path . peer.py)",
        "Move-Item -Path (Join-Path . peer.py) -Destination out",
    ),
)
def test_powershell_expression_path_flags_fail_closed(
    tmp_path: Path,
    command: str,
) -> None:
    # Given: a path-bearing PowerShell flag receives a non-static expression.
    # When: the expression reaches the gate without a resolvable literal target.
    decision = _decision(tmp_path, command, None)

    # Then: the command fails closed instead of bypassing attribution.
    assert decision == "block"


@pytest.mark.parametrize(
    "command",
    (
        "Clear-Content -Path peer.py",
        "Clear-Content -LiteralPath peer.py",
        "Copy-Item mine.py -Destination peer.py",
        "Move-Item -Path peer.py -Destination out",
    ),
)
def test_powershell_static_path_flags_keep_peer_attribution(
    tmp_path: Path,
    command: str,
) -> None:
    # Given: the same path-bearing flags receive a static peer-owned path.
    # When: the command reaches attribution routing.
    decision = _decision(tmp_path, command, "peer.py")

    # Then: the existing static-path behavior remains blocked.
    assert decision == "block"


@pytest.mark.parametrize("quote", ('"', "'"))
def test_quoted_parenthesized_path_remains_static(quote: str) -> None:
    # Given: a literal path begins with a parenthesis but is quoted.
    # When: the path-bearing flag is parsed.
    parsed = parse_destructive_command(f"Clear-Content -Path {quote}(literal).py{quote}")

    # Then: quote context distinguishes the filename from a PowerShell expression.
    assert parsed is not None
    assert parsed.resolved is True
    assert parsed.targets == ("(literal).py",)


@pytest.mark.parametrize(
    "command",
    (
        "sed -ibak s/x/y/ peer.py",
        "sed -i'' s/x/y/ peer.py",
        "sed -i.bak s/x/y/ peer.py",
        "perl -ibak -pe s/x/y/ peer.py",
    ),
)
def test_in_place_suffix_forms_route_peer_owned_files(
    tmp_path: Path,
    command: str,
) -> None:
    # Given: sed or perl uses a documented in-place suffix form.
    # When: the edited file is owned by an unsettled peer.
    decision = _decision(tmp_path, command, "peer.py")

    # Then: suffix spelling cannot bypass destructive attribution.
    assert decision == "block"


def test_non_in_place_sed_remains_allowed_for_peer_path(tmp_path: Path) -> None:
    # Given: sed only streams transformed output and does not edit in place.
    # When: its input file is peer-owned.
    decision = _decision(tmp_path, "sed s/x/y/ peer.py", "peer.py")

    # Then: read-only transformation stays outside the destructive gate.
    assert decision == "allow"


@pytest.mark.parametrize(
    ("command", "targets"),
    (
        ("cp mine.py out", ("out", "out/mine.py")),
        ("mv mine.py out", ("mine.py", "out", "out/mine.py")),
        ("Copy-Item mine.py out", ("out", "out/mine.py")),
    ),
)
def test_single_source_transfer_routes_directory_candidate(
    command: str,
    targets: tuple[str, ...],
) -> None:
    # Given: a single source is transferred to a destination without a slash.
    # When: the parser builds attribution candidates without filesystem stat.
    parsed = parse_destructive_command(command)

    # Then: both the literal destination and destination/source-basename are routed.
    assert parsed is not None
    assert parsed.targets == targets


@pytest.mark.parametrize(
    "command",
    (
        "cp mine.py out",
        "mv mine.py out",
        "Copy-Item mine.py out",
    ),
)
def test_single_source_transfer_blocks_peer_owned_directory_candidate(
    tmp_path: Path,
    command: str,
) -> None:
    # Given: a peer owns the possible destination/source-basename path.
    # When: the slashless destination reaches the gate.
    decision = _decision(tmp_path, command, "out/mine.py")

    # Then: the candidate is checked and the transfer is blocked.
    assert decision == "block"


@pytest.mark.parametrize("quote", ('"', "'"))
def test_quoted_powershell_comma_is_one_literal_path(quote: str) -> None:
    # Given: a PowerShell path containing a comma is quoted as one argument.
    # When: the parser resolves the Remove-Item path.
    parsed = parse_destructive_command(f"Remove-Item {quote}a,b.py{quote}")

    # Then: the comma remains part of one literal filename.
    assert parsed is not None
    assert parsed.targets == ("a,b.py",)


def test_quoted_powershell_comma_routes_literal_peer_path(tmp_path: Path) -> None:
    # Given: the quoted comma filename is owned by an unsettled peer.
    # When: Remove-Item reaches the gate.
    decision = _decision(tmp_path, 'Remove-Item "a,b.py"', "a,b.py")

    # Then: ownership of the literal filename blocks removal.
    assert decision == "block"


def test_unquoted_powershell_comma_still_routes_array_members() -> None:
    # Given: an unquoted comma separates two PowerShell path values.
    # When: the parser resolves the Remove-Item paths.
    parsed = parse_destructive_command("Remove-Item mine1.py,mine2.py")

    # Then: both array members remain independent attribution candidates.
    assert parsed is not None
    assert parsed.targets == ("mine1.py", "mine2.py")
