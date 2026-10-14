"""R2: an output redirection must not hide the command's own destructive target.

The segment parser used to return only the first detector hit, and the
redirect detector ran before the delete detector, so `rm peer.py 2>/dev/null`
was judged by `/dev/null` alone and the peer's file was never checked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_hook_support import HookHarness, write_config
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


# --- Codex review r1 follow-up -------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["Remove-Item peer.py *>$null", "Remove-Item peer.py 2>$null"],
)
def test_powershell_discard_does_not_mask_the_delete(tmp_path: Path, command: str) -> None:
    result = r2_gate(tmp_path, command, tool_name="PowerShell")

    assert result["decision"] == "block", command
    assert result["coordination_reason_code"] == "peer_unsettled"


@pytest.mark.parametrize("command", ["Get-ChildItem 2>$null", "Remove-Item own.py *>$NULL"])
def test_powershell_tool_may_discard_output_to_null(tmp_path: Path, command: str) -> None:
    assert r2_gate(tmp_path, command, tool_name="PowerShell")["decision"] == "allow", command


@pytest.mark.parametrize(
    "command",
    [
        "null=peer.py; echo x >$null",
        "null=.smtw/ledger.json; echo x >$null",
        "echo x 2>$null",
    ],
)
def test_dollar_null_is_a_variable_unless_the_host_says_powershell(
    tmp_path: Path, command: str
) -> None:
    # In bash `$null` expands to whatever `null` holds, so the target is unknown.
    result = r2_gate(tmp_path, command)

    assert result["decision"] == "block", command
    assert result["coordination_reason_code"] == "command_parse_unavailable"


@pytest.mark.parametrize(
    ("tool_name", "denied"),
    [("PowerShell", False), ("Bash", True)],
)
def test_claude_adapter_reports_the_powershell_tool_to_r2(
    tmp_path: Path, tool_name: str, denied: bool
) -> None:
    # Given: the same discard idiom sent through Claude's PowerShell and Bash tools.
    root = tmp_path / "project"
    root.mkdir()
    _ = write_config(root)
    payload = {
        "session_id": "followup-null",
        "cwd": str(root),
        "tool_name": tool_name,
        "tool_input": {"command": "Get-ChildItem 2>$null"},
    }

    # When: the real PreTool adapter evaluates it.
    result = HookHarness(root, root, tmp_path / "plugin-data").run("pre_tool_use.py", payload)

    # Then: only the PowerShell tool may treat `$null` as a discard.
    decision = result.output.get("hookSpecificOutput")
    reason = str(decision.get("permissionDecisionReason")) if isinstance(decision, dict) else ""
    assert ("parse_unable_target" in reason) is denied, result.output


def test_powershell_exception_does_not_reach_a_nested_posix_shell() -> None:
    parsed = parse_destructive_commands(
        "bash -c 'null=peer.py; echo x >$null'", powershell=True
    )

    assert any(not item.resolved for item in parsed), parsed


@pytest.mark.parametrize(
    "command",
    [
        "echo x > own.log>peer.py",
        "echo x > own.log >peer.py",
        "echo x >own.log>peer.py",
        "echo x 2> own.log>peer.py",
        "echo x > own.log<in>peer.py",
    ],
)
def test_redirect_target_word_is_split_again(tmp_path: Path, command: str) -> None:
    result = r2_gate(tmp_path, command)

    assert result["decision"] == "block", command
    assert result["coordination_reason_code"] == "peer_unsettled"


def test_redirect_chain_into_state_dir_is_blocked(tmp_path: Path) -> None:
    result = r2_gate(tmp_path, "echo x > own.log>.smtw/ledger.json")

    assert result["decision"] == "block"
    assert result["coordination_reason_code"] == "state_dir_protected"


def test_redirect_chain_reports_every_target() -> None:
    parsed = parse_destructive_commands("echo x > own.log>peer.py 2>err.log")

    assert [item.targets for item in parsed] == [("own.log", "peer.py", "err.log")]


@pytest.mark.parametrize(
    "command",
    ["rm peer.py<in", "rm peer.py <in", "echo x > peer.py<in", "cat <<<x>peer.py"],
)
def test_input_redirection_does_not_disguise_the_target(
    tmp_path: Path, command: str
) -> None:
    assert r2_gate(tmp_path, command)["decision"] == "block", command


@pytest.mark.parametrize(
    "command",
    [
        r"echo safe\>peer.py",
        r"echo safe \> peer.py",
        r"echo safe\>.smtw/ledger.json",
        r"printf '%s' a\>\>peer.py",
    ],
)
def test_escaped_redirect_character_is_literal(tmp_path: Path, command: str) -> None:
    # bash prints `safe>peer.py`; PowerShell also treats a glued `>` as text.
    assert r2_gate(tmp_path, command)["decision"] == "allow", command


def test_escaped_redirect_character_is_not_a_target() -> None:
    assert parse_destructive_commands(r"echo safe\>peer.py") == ()
    # Only the escaped `>` is literal; the following real one still counts.
    parsed = parse_destructive_commands(r"echo a\>b >peer.py")
    assert [item.targets for item in parsed] == [("peer.py",)]


def test_backslash_inside_double_quotes_keeps_the_quote_closing() -> None:
    # PowerShell ends `"C:\dir\"` at the last quote; the glued `>` must count.
    parsed = parse_destructive_commands(r'echo pre"C:\dir\">peer.py')

    assert [item.targets for item in parsed] == [("peer.py",)]
