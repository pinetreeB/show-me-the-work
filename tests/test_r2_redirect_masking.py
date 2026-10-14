"""R2: an output redirection must not hide the command's own destructive target.

The segment parser used to return only the first detector hit, and the
redirect detector ran before the delete detector, so `rm peer.py 2>/dev/null`
was judged by `/dev/null` alone and the peer's file was never checked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.destructive_guard import parse_destructive_commands
from r2_peer_support import r2_gate


@pytest.mark.parametrize(
    "command",
    [
        "rm peer.py > own.log",
        "rm peer.py 2>/dev/null",
        "rm -f peer.py 2>&1",
        "rm peer.py>own.log",
        ">own.log rm peer.py",
        "rm peer.py &>/dev/null",
        "sed -i s/x/y/ peer.py 2>/dev/null",
        "mv peer.py moved.py 2>/dev/null",
        "cp mine.py peer.py > copy.log",
        "Remove-Item peer.py *>$null",
        "(true && rm peer.py)",
    ],
)
def test_redirect_does_not_mask_destructive_target(tmp_path: Path, command: str) -> None:
    result = r2_gate(tmp_path, command)

    assert result["decision"] == "block", command
    assert result["coordination_reason_code"] == "peer_unsettled"


@pytest.mark.parametrize(
    "command",
    [
        "rm own.py 2>/dev/null",
        "rm own.py > own.log",
        "git status 2>&1",
        "ls -la > listing.txt",
        "Get-ChildItem 2>$null",
        "echo done >&2",
        "rm own.py 2>>err.log",
    ],
)
def test_redirect_split_keeps_own_work_allowed(tmp_path: Path, command: str) -> None:
    assert r2_gate(tmp_path, command)["decision"] == "allow", command


def test_every_redirect_target_and_the_command_target_are_reported() -> None:
    parsed = parse_destructive_commands("rm a.txt > b.txt 2> c.txt")

    assert [item.targets for item in parsed] == [("a.txt",), ("b.txt", "c.txt")]


@pytest.mark.parametrize(
    ("command", "targets"),
    [
        ("echo x>peer.py", ("peer.py",)),
        ("echo 'x'>peer.py", ("peer.py",)),
        ("a>b>c", ("b", "c")),
        ("echo x >| peer.py", ("peer.py",)),
    ],
)
def test_redirect_glued_to_a_word_is_detected(
    command: str, targets: tuple[str, ...]
) -> None:
    parsed = parse_destructive_commands(command)

    assert [item.targets for item in parsed] == [targets]


@pytest.mark.parametrize(
    "command",
    ['echo "a>b"', "echo 'a > b'", "echo x>>peer.py", "cat <> own.txt", "echo $((1))"],
)
def test_quoted_append_and_read_write_redirects_are_not_truncations(command: str) -> None:
    assert parse_destructive_commands(command) == ()


def test_glued_subshell_parens_do_not_change_the_target() -> None:
    parsed = parse_destructive_commands("(rm peer.py)")

    assert [item.targets for item in parsed] == [("peer.py",)]


def test_glued_redirect_into_state_dir_is_blocked(tmp_path: Path) -> None:
    result = r2_gate(tmp_path, "echo x>.smtw/ledger.json")

    assert result["decision"] == "block"
    assert result["coordination_reason_code"] == "state_dir_protected"
