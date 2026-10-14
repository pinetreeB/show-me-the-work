"""R2: relative targets are resolved where the shell really stands.

Relative targets used to be resolved only against the project root, so
`cd src && rm peer.py` checked `<root>/peer.py` and let `src/peer.py` go, and
`cd .smtw && rm ledger.json` slipped past the state-dir protection.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claude_hook_support import HookHarness, write_config
from core.destructive_guard import (
    _msys_drive_path,
    command_working_directories,
    host_shell_cwd,
    path_interpretations,
    working_directory_bases,
)
from r2_peer_support import r2_gate


# Only the nested file is protected, so a test cannot pass by resolving the
# relative target against the project root by mistake.
SRC_PEER_ONLY = frozenset({"src/peer.py"})
NESTED_PEER_ONLY = frozenset({"src/src/peer.py"})


@pytest.mark.parametrize(
    "command",
    [
        "cd src && rm peer.py",
        "cd src; rm -f peer.py 2>/dev/null",
        "cd ./src && cd . && rm peer.py",
        "pushd src && rm peer.py && popd",
        "Set-Location src; Remove-Item peer.py",
        "Set-Location -Path src; Remove-Item peer.py",
        "(cd src && rm peer.py)",
        "cd src && mv peer.py ../moved.py",
    ],
)
def test_relative_target_after_cd_is_checked_in_the_new_directory(
    tmp_path: Path, command: str
) -> None:
    result = r2_gate(tmp_path, command, peer_paths=SRC_PEER_ONLY)

    assert result["decision"] == "block", command


def test_precise_fixture_does_not_protect_the_root_file(tmp_path: Path) -> None:
    # Control: without the cd, `peer.py` is the unprotected root file.
    assert r2_gate(tmp_path, "rm peer.py", peer_paths=SRC_PEER_ONLY)["decision"] == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "cd src && cd src && rm peer.py",
        "cd src; cd src; rm peer.py",
        "pushd src && pushd src && rm peer.py",
        "cd src && cd lib && cd .. && cd src && rm peer.py",
    ],
)
def test_repeated_cd_is_followed_step_by_step(tmp_path: Path, command: str) -> None:
    result = r2_gate(tmp_path, command, peer_paths=NESTED_PEER_ONLY)

    assert result["decision"] == "block", command


def test_single_cd_does_not_reach_the_nested_directory(tmp_path: Path) -> None:
    result = r2_gate(tmp_path, "cd src && rm peer.py", peer_paths=NESTED_PEER_ONLY)

    assert result["decision"] == "allow"


def test_cd_into_state_dir_then_relative_delete_is_blocked(tmp_path: Path) -> None:
    result = r2_gate(tmp_path, "cd .smtw && rm ledger.json")

    assert result["decision"] == "block"
    assert result["coordination_reason_code"] == "state_dir_protected"


def test_cd_to_absolute_project_subdirectory_is_followed(tmp_path: Path) -> None:
    command = f'cd "{(tmp_path / "src").as_posix()}" && rm peer.py'

    assert r2_gate(tmp_path, command)["decision"] == "block"


@pytest.mark.parametrize(
    "command",
    [
        "cd src && rm own.py",
        "cd src && ls -la > listing.txt",
        "cd src && cat peer.py > ../copy.txt",
        "cd .. && rm unrelated.txt",
        "cd $BUILD_DIR && ls",
        "cd $BUILD_DIR && cat notes.txt > /dev/null",
    ],
)
def test_cd_keeps_unrelated_work_allowed(tmp_path: Path, command: str) -> None:
    assert r2_gate(tmp_path, command)["decision"] == "allow", command


@pytest.mark.parametrize(
    "command",
    ["cd $BUILD_DIR && rm out.txt", "cd - && rm out.txt", "popd && rm out.txt"],
)
def test_unresolvable_cd_with_relative_destructive_target_fails_closed(
    tmp_path: Path, command: str
) -> None:
    result = r2_gate(tmp_path, command)

    assert result["decision"] == "block"
    assert result["coordination_reason_code"] == "command_parse_unavailable"


def test_host_shell_cwd_is_used_for_relative_targets(tmp_path: Path) -> None:
    shell_cwd = str(tmp_path / "src")

    blocked = r2_gate(tmp_path, "rm peer.py", shell_cwd=shell_cwd, peer_paths=SRC_PEER_ONLY)
    allowed = r2_gate(tmp_path, "rm own.py", shell_cwd=shell_cwd, peer_paths=SRC_PEER_ONLY)

    assert blocked["decision"] == "block"
    assert allowed["decision"] == "allow"


def test_cd_after_host_shell_cwd_is_applied_on_top_of_it(tmp_path: Path) -> None:
    result = r2_gate(
        tmp_path,
        "cd src && rm peer.py",
        shell_cwd=str(tmp_path / "src"),
        peer_paths=NESTED_PEER_ONLY,
    )

    assert result["decision"] == "block"


def test_host_shell_cwd_inside_state_dir_protects_state(tmp_path: Path) -> None:
    result = r2_gate(tmp_path, "rm ledger.json", shell_cwd=str(tmp_path / ".smtw"))

    assert result["decision"] == "block"
    assert result["coordination_reason_code"] == "state_dir_protected"


def test_command_working_directories_reports_static_and_dynamic_cd() -> None:
    assert command_working_directories("cd src && pushd lib && popd") == (
        ("src", "lib"),
        False,
    )
    assert command_working_directories("cd ${OUT} && ls")[1] is True
    assert command_working_directories("ls -la") == ((), False)


def test_command_working_directories_keep_repeats_in_order() -> None:
    assert command_working_directories("cd src && cd src && cd lib && cd src") == (
        ("src", "src", "lib", "src"),
        False,
    )


def test_working_directory_bases_follow_a_repeated_cd(tmp_path: Path) -> None:
    root = str(tmp_path)

    bases, unknown = working_directory_bases(root, "cd src && cd src && rm x")

    assert unknown is False
    assert os.path.abspath(os.path.join(root, "src", "src")) in bases


def test_working_directory_bases_keep_root_first(tmp_path: Path) -> None:
    root = str(tmp_path)

    bases, unknown = working_directory_bases(root, "cd src && cd lib && rm x")

    assert unknown is False
    assert bases[0] == os.path.abspath(root)
    assert os.path.abspath(os.path.join(root, "src", "lib")) in bases
    assert os.path.abspath(os.path.join(root, "lib")) in bases


def test_path_interpretations_leave_absolute_paths_alone(tmp_path: Path) -> None:
    bases = (str(tmp_path), str(tmp_path / "src"))
    absolute = (tmp_path / "x.py").as_posix()

    assert path_interpretations(absolute, bases, windows=False) == (absolute,)
    assert path_interpretations("x.py", bases, windows=False) == (
        "x.py",
        os.path.join(str(tmp_path / "src"), "x.py"),
    )


def test_git_bash_drive_paths_are_translated_on_windows_only() -> None:
    assert _msys_drive_path("/c/Users/me/proj", windows=True) == "C:/Users/me/proj"
    assert _msys_drive_path("/d", windows=True) == "D:/"
    assert _msys_drive_path("/c/Users/me/proj", windows=False) is None
    assert _msys_drive_path("/usr/local", windows=True) is None


def test_host_shell_cwd_reads_only_string_cwd() -> None:
    assert host_shell_cwd({"cwd": "/work/proj"}) == "/work/proj"
    assert host_shell_cwd({"cwd": 3}) == ""
    assert host_shell_cwd({}) == ""


def test_claude_adapter_checks_relative_target_in_host_shell_cwd(tmp_path: Path) -> None:
    # Given: Claude's shell already stands inside the state directory.
    root = tmp_path / "project"
    root.mkdir()
    config = write_config(root)
    payload = {
        "session_id": "followup-cwd",
        "cwd": str(config.parent),
        "tool_name": "Bash",
        "tool_input": {"command": f"rm {config.name}"},
    }

    # When: the real PreTool adapter evaluates a bare relative delete.
    result = HookHarness(root, root, tmp_path / "plugin-data").run("pre_tool_use.py", payload)

    # Then: the adapter forwarded the shell cwd and R2 protected the state file.
    decision = result.output.get("hookSpecificOutput")
    assert isinstance(decision, dict), result.output
    assert decision.get("permissionDecision") == "deny"
    assert "state_dir_protected" in str(decision.get("permissionDecisionReason"))
    assert config.exists()


# --- Codex review r2 follow-up: input redirection on cd ---------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cd .smtw < /dev/null && echo x", ((".smtw",), False)),
        ("cd src </dev/null && rm peer.py", (("src",), False)),
        ("< /dev/null cd src && rm peer.py", (("src",), False)),
    ],
)
def test_input_redirection_is_not_a_cd_operand(
    command: str, expected: tuple[tuple[str, ...], bool]
) -> None:
    assert command_working_directories(command) == expected


@pytest.mark.parametrize(
    "command",
    ["cd src < /dev/null && rm peer.py", "< /dev/null cd src && rm peer.py"],
)
def test_cd_with_input_redirection_checks_the_new_directory(
    tmp_path: Path, command: str
) -> None:
    result = r2_gate(tmp_path, command, peer_paths=SRC_PEER_ONLY)

    assert result["decision"] == "block", command
    assert result["coordination_reason_code"] == "peer_unsettled"


# --- Command substitution with backticks in a cd target ---------------------


@pytest.mark.parametrize(
    "command",
    [
        "cd `pwd`/src && rm out.txt",
        'cd "`pwd`/src" && rm out.txt',
        "cd src/`echo x` && rm out.txt",
    ],
)
def test_backtick_cd_target_is_unresolvable(tmp_path: Path, command: str) -> None:
    # `pwd` may print any directory, so the new working directory is unknown.
    assert command_working_directories(command)[1] is True, command
    result = r2_gate(tmp_path, command)

    assert result["decision"] == "block", command
    assert result["coordination_reason_code"] == "command_parse_unavailable"
