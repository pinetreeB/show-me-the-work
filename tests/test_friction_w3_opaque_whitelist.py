from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from core.destructive_guard import (
    evaluate_r2_destructive_gate,
    parse_destructive_commands,
)
from core.shell_command import is_proven_read_only_command


ATTACK_CASES: tuple[tuple[str, str], ...] = (
    ("chain-cmd-ampersand", 'cmd.exe /c "dir & echo owned > marker.txt"'),
    (
        "chain-pwsh-semicolon",
        'pwsh -NoProfile -NonInteractive -Command "Get-Content README.md; '
        +
        'Add-Content README.md owned"',
    ),
    (
        "chain-powershell-pipeline",
        'powershell.exe -NoProfile -NonInteractive -Command "Get-ChildItem . | '
        +
        'Copy-Item -Destination backup"',
    ),
    (
        "dynamic-iex",
        "pwsh -NoProfile -NonInteractive -Command "
        +
        "\"Invoke-Expression 'Add-Content marker.txt owned'\"",
    ),
    (
        "dynamic-call-operator",
        'pwsh -NoProfile -NonInteractive -Command "& .\\payload.ps1"',
    ),
    (
        "dynamic-dot-source",
        'powershell -NoProfile -NonInteractive -Command ". .\\payload.ps1"',
    ),
    (
        "expression-subexpression",
        'pwsh -NoProfile -NonInteractive -Command "Get-Content '
        +
        '$(Join-Path . README.md)"',
    ),
    (
        "expression-scriptblock",
        "pwsh -NoProfile -NonInteractive -Command "
        +
        "\"@('README.md') | ForEach-Object { Add-Content marker.txt $_ }\"",
    ),
    (
        "expression-splatting",
        'powershell -NoProfile -NonInteractive -Command "Get-Content @args"',
    ),
    (
        "mode-encoded-command",
        "pwsh -EncodedCommand RwBlAHQALQBDAG8AbgB0AGUAbgB0AA==",
    ),
    ("mode-file", r"powershell -NoProfile -File .\payload.ps1"),
    (
        "mode-profile-enabled",
        'pwsh -NonInteractive -Command "Get-Content README.md"',
    ),
    ("cmd-expansion-percent", 'cmd.exe /c "echo %COMSPEC%"'),
    ("cmd-expansion-delayed", 'cmd.exe /v:on /c "echo !PAYLOAD!"'),
    ("cmd-expansion-call", 'cmd.exe /c "call echo %%PATH%%"'),
    ("escape-caret", 'cmd.exe /c "echo safe ^& echo owned"'),
    (
        "escape-backtick",
        'pwsh -NoProfile -NonInteractive -Command "Get-Content `$PROFILE"',
    ),
    (
        "escape-newline",
        'pwsh -NoProfile -NonInteractive -Command "Get-Content README.md\n'
        +
        'Add-Content marker.txt owned"',
    ),
    ("redirect-cmd", 'cmd.exe /c "echo owned > marker.txt"'),
    (
        "redirect-pwsh-all-streams",
        'pwsh -NoProfile -NonInteractive -Command "Get-Content README.md '
        +
        '*> marker.txt"',
    ),
    (
        "redirect-tee",
        'pwsh -NoProfile -NonInteractive -Command "Get-Content README.md | '
        +
        'Tee-Object marker.txt"',
    ),
    (
        "path-parent",
        'pwsh -NoProfile -NonInteractive -Command "Get-Content '
        +
        '..\\secret.txt"',
    ),
    (
        "path-ads",
        'pwsh -NoProfile -NonInteractive -Command "Get-Content '
        +
        'C:\\repo\\file.txt:secret"',
    ),
    ("path-unc", r'cmd.exe /c "dir \\server\share"'),
    (
        "alias-powershell-gci",
        'pwsh -NoProfile -NonInteractive -Command "gci ."',
    ),
    (
        "alias-powershell-cat",
        'powershell -NoProfile -NonInteractive -Command "cat README.md"',
    ),
    ("alias-relative-cmd", r'cmd.exe /c ".\dir.cmd"'),
    (
        "pipeline-add-content",
        'pwsh -NoProfile -NonInteractive -Command "Get-Content README.md | '
        +
        'Add-Content marker.txt"',
    ),
    (
        "pipeline-foreach",
        'pwsh -NoProfile -NonInteractive -Command "Get-ChildItem . | '
        +
        'ForEach-Object { Remove-Item $_ }"',
    ),
    ("pipeline-cmd-chain", 'cmd.exe /c "dir | more & del marker.txt"'),
)

OPAQUE_UNKNOWN_CASES: tuple[str, ...] = (
    ATTACK_CASES[1][1],
    ATTACK_CASES[2][1],
    ATTACK_CASES[3][1],
    ATTACK_CASES[4][1],
    ATTACK_CASES[5][1],
    ATTACK_CASES[6][1],
    ATTACK_CASES[8][1],
    ATTACK_CASES[9][1],
    ATTACK_CASES[10][1],
    ATTACK_CASES[12][1],
    ATTACK_CASES[13][1],
    ATTACK_CASES[15][1],
    ATTACK_CASES[16][1],
    ATTACK_CASES[21][1],
    ATTACK_CASES[22][1],
    ATTACK_CASES[23][1],
    ATTACK_CASES[24][1],
    ATTACK_CASES[25][1],
    ATTACK_CASES[26][1],
    ATTACK_CASES[27][1],
)

SAFE_CASES: tuple[str, ...] = (
    "cat README.md",
    "rg --no-config -n friction README.md",
    "git rev-parse --show-toplevel",
    "cmd.exe /c dir",
    'cmd /c "echo hello"',
    'bash -c "printf safe"',
    "bash -c \"builtin printf '%s\\n' rm peer.py\"",
    'dash -c "echo safe"',
    'pwsh -NoProfile -NonInteractive -Command "Get-Content README.md"',
    'powershell.exe -NoProfile -NonInteractive -Command "Get-ChildItem '
    +
    '-Recurse . | Select-String -Pattern friction"',
)


def _payload(root: Path, command: str) -> dict[str, str]:
    return {
        "project_root": str(root),
        "tool_name": "PowerShell",
        "command": command,
        "host": "codex_cli",
        "session_id": "friction-w3",
        "agent": "codex",
    }


def test_attack_corpus_has_ten_families_with_three_variants_each() -> None:
    counts = Counter(case_id.split("-", 1)[0] for case_id, _command in ATTACK_CASES)

    assert len(ATTACK_CASES) == 30
    assert set(counts.values()) == {3}


@pytest.mark.parametrize(("case_id", "command"), ATTACK_CASES)
def test_opaque_wrapper_attack_corpus_is_blocked(
    tmp_path: Path,
    case_id: str,
    command: str,
) -> None:
    decision = evaluate_r2_destructive_gate(_payload(tmp_path, command))

    assert decision["decision"] == "block", case_id


@pytest.mark.parametrize("command", OPAQUE_UNKNOWN_CASES)
def test_opaque_unknown_uses_existing_parse_unavailable_label(
    tmp_path: Path,
    command: str,
) -> None:
    parsed = parse_destructive_commands(command)
    decision = evaluate_r2_destructive_gate(_payload(tmp_path, command))

    assert any(item.reason == "parse_unable_wrapped" for item in parsed)
    assert decision["coordination_reason_code"] == "command_parse_unavailable"


@pytest.mark.parametrize("command", ATTACK_CASES)
def test_shared_classifier_rejects_every_attack(command: tuple[str, str]) -> None:
    _case_id, raw_command = command

    assert is_proven_read_only_command(raw_command) is False


@pytest.mark.parametrize("command", SAFE_CASES)
def test_shared_classifier_and_r2_allow_literal_read_only_wrappers(
    tmp_path: Path,
    command: str,
) -> None:
    decision = evaluate_r2_destructive_gate(_payload(tmp_path, command))

    assert is_proven_read_only_command(command) is True
    assert decision["decision"] == "allow"
