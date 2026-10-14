"""State-file friction follow-up.

`.smtw/**` friction missed write commands that R2 does not model (`install`,
`patch`, `curl -o`, `wget -O`), redirections glued to a word (`x>>.smtw/f`) and
relative paths written after `cd .smtw`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claude_hook_support import HookHarness, write_config
from core.contract import evaluate_state_file_friction
from core.shell_hints import shell_candidate_paths


def _friction(root: Path, command: str, **extra: str) -> dict[str, object]:
    return evaluate_state_file_friction(
        {
            "project_root": str(root),
            "tool_name": "Bash",
            "command": command,
            **extra,
        }
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("echo x>.smtw/ledger.json", ".smtw/ledger.json"),
        ("echo x>>.smtw/ledger.json", ".smtw/ledger.json"),
        ("install -m 644 build/a .smtw/ledger.json", ".smtw/ledger.json"),
        ("install -t .smtw/bin build/a", ".smtw/bin"),
        ("install --target-directory=.smtw build/a", ".smtw"),
        ("install -d .smtw/new", ".smtw/new"),
        ("patch .smtw/ledger.json < fix.diff", ".smtw/ledger.json"),
        ("patch -o .smtw/out.json in.json fix.diff", ".smtw/out.json"),
        ("patch -d .smtw -p1 < fix.diff", ".smtw"),
        ("curl -o .smtw/ledger.json https://example.invalid/x", ".smtw/ledger.json"),
        ("curl -sSLo .smtw/ledger.json https://example.invalid/x", ".smtw/ledger.json"),
        ("curl --output=.smtw/ledger.json https://example.invalid/x", ".smtw/ledger.json"),
        ("curl --output-dir .smtw -O https://example.invalid/x", ".smtw"),
        ("wget -O .smtw/ledger.json https://example.invalid/x", ".smtw/ledger.json"),
        ("wget -P .smtw https://example.invalid/x", ".smtw"),
        ("wget --output-document=.smtw/l https://example.invalid/x", ".smtw/l"),
    ],
)
def test_write_command_hints_include_state_destination(command: str, expected: str) -> None:
    assert expected in shell_candidate_paths(command)


@pytest.mark.parametrize(
    "command",
    [
        "curl -O https://example.invalid/x",
        "curl -H 'Accept: x' https://example.invalid/x",
        "wget -qO- https://example.invalid/x",
        "patch -p1 -i fix.diff",
    ],
)
def test_write_command_hints_ignore_stdout_and_unknown_destinations(command: str) -> None:
    assert shell_candidate_paths(command) == ()


@pytest.mark.parametrize(
    "command",
    [
        "echo x>>.smtw/ledger.json",
        "echo x >> .smtw/ledger.json",
        "install build/a .smtw/ledger.json",
        "patch .smtw/ledger.json < fix.diff",
        "curl -o .smtw/ledger.json https://example.invalid/x",
        "wget -O .fable-lite/ledger.json https://example.invalid/x",
        "cd .smtw && echo x >> ledger.json",
        "cd .smtw; curl -o ledger.json https://example.invalid/x",
    ],
)
def test_state_friction_blocks_write_commands(tmp_path: Path, command: str) -> None:
    result = _friction(tmp_path, command)

    assert result["decision"] == "block", command
    assert "마찰 장치" in str(result["reason"])


def test_state_friction_uses_host_shell_cwd(tmp_path: Path) -> None:
    result = _friction(
        tmp_path,
        "echo x >> ledger.json",
        shell_cwd=str(tmp_path / ".smtw"),
    )

    assert result["decision"] == "block"


@pytest.mark.parametrize(
    "command",
    [
        "curl -o build/out.json https://example.invalid/x",
        "install -m 755 build/tool bin/tool",
        "patch src/app.py < fix.diff",
        "cat .smtw/ledger.json",
        "cd src && echo x >> notes.txt",
        "grep -n smtw .smtw/ledger.json > report.txt",
    ],
)
def test_state_friction_allows_reads_and_project_writes(tmp_path: Path, command: str) -> None:
    assert _friction(tmp_path, command)["decision"] == "allow", command


# --- Codex review r1 follow-up ------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("install file .smtw/ledger.json > own.log", ".smtw/ledger.json"),
        ("install file .smtw/ledger.json>own.log", ".smtw/ledger.json"),
        ("install file .smtw/ledger.json 2> err.log", ".smtw/ledger.json"),
        ("install file .smtw/ledger.json &> all.log", ".smtw/ledger.json"),
        ("install file .smtw/ledger.json 2>&1", ".smtw/ledger.json"),
        ("install -t .smtw/bin file > own.log", ".smtw/bin"),
        ("patch .smtw/ledger.json < fix.diff > patch.log", ".smtw/ledger.json"),
        ("curl -o .smtw/ledger.json https://example.invalid/x > c.log", ".smtw/ledger.json"),
        ("echo x > own.log>.smtw/ledger.json", ".smtw/ledger.json"),
    ],
)
def test_output_redirection_is_not_read_as_the_write_destination(
    tmp_path: Path, command: str, expected: str
) -> None:
    assert expected in shell_candidate_paths(command), command
    assert _friction(tmp_path, command)["decision"] == "block", command


def test_install_hints_drop_redirect_operators_and_descriptors() -> None:
    assert shell_candidate_paths("install a b 2> err.log") == ("err.log", "b")


# --- Codex review r2 follow-up ------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "cd .smtw < /dev/null && echo x >> ledger.json",
        "cd .smtw </dev/null && echo x >> ledger.json",
        "< /dev/null cd .smtw && echo x >> ledger.json",
    ],
)
def test_cd_with_input_redirection_still_protects_state_files(
    tmp_path: Path, command: str
) -> None:
    assert _friction(tmp_path, command)["decision"] == "block", command


@pytest.mark.parametrize(
    "command",
    [
        'cd "$DIR" && echo x >> ledger.json',
        "cd - && echo x > ledger.json",
        "popd && echo x >> ledger.json",
        'cd "$DIR" && install build/a ledger.json',
        'cd "$(git rev-parse --show-toplevel)" && echo x > out.txt',
    ],
)
def test_relative_write_after_an_unresolved_cd_is_blocked(
    tmp_path: Path, command: str
) -> None:
    # The unknown directory may be `.smtw`, so a relative write cannot be judged.
    result = _friction(tmp_path, command)

    assert result["decision"] == "block", command
    assert "절대경로" in str(result["reason"])


@pytest.mark.parametrize(
    "command",
    [
        'cd "$DIR" && ls',
        'cd "$DIR" && cat ledger.json',
        'cd "$DIR" && echo x >> /tmp/smtw-followup.log',
        'cd "$DIR" && echo x > C:/logs/out.txt',
        'cd "$DIR" && make 2>/dev/null',
        'cd "$DIR" && echo x > "$OUT"',
        'cd "$DIR" && echo x > ~/notes.txt',
    ],
)
def test_unresolved_cd_still_allows_reads_and_anchored_writes(
    tmp_path: Path, command: str
) -> None:
    assert _friction(tmp_path, command)["decision"] == "allow", command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("install -Dt .smtw file", ".smtw"),
        ("install -Dt.smtw file", ".smtw"),
        ("install -Dvt .smtw file", ".smtw"),
        ("install -Dm644 file -t .smtw", ".smtw"),
        ("install --target=.smtw file", ".smtw"),
        ("install --targ .smtw file", ".smtw"),
        ("install -Dd .smtw/new", ".smtw/new"),
        ("install --dir .smtw/new", ".smtw/new"),
        ("patch -Ed.smtw -p0 < fix.diff", ".smtw"),
        ("patch --dir=.smtw -p0 < fix.diff", ".smtw"),
        ("patch -No .smtw/out.json in.json fix.diff", ".smtw/out.json"),
        ("patch --out .smtw/out.json in.json fix.diff", ".smtw/out.json"),
    ],
)
def test_bundled_and_abbreviated_destination_options_are_decoded(
    tmp_path: Path, command: str, expected: str
) -> None:
    assert expected in shell_candidate_paths(command), command
    assert _friction(tmp_path, command)["decision"] == "block", command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("install -Dm644 build/tool bin/tool", ("bin/tool",)),
        ("install -Dm 644 build/tool bin/tool", ("bin/tool",)),
        ("install -Dt bin build/tool", ("bin",)),
        ("patch -p1 -i fix.diff", ()),
    ],
)
def test_bundled_options_keep_project_writes_allowed(
    tmp_path: Path, command: str, expected: tuple[str, ...]
) -> None:
    assert shell_candidate_paths(command) == expected
    assert _friction(tmp_path, command)["decision"] == "allow", command


@pytest.mark.parametrize(
    "command",
    [
        "cd `pwd`/.smtw && echo x > ledger.json",
        'cd "`pwd`/.smtw" && echo x >> ledger.json',
    ],
)
def test_relative_write_after_a_backtick_cd_is_blocked(
    tmp_path: Path, command: str
) -> None:
    result = _friction(tmp_path, command)

    assert result["decision"] == "block", command
    assert "절대경로" in str(result["reason"])


def test_backtick_cd_still_allows_reads(tmp_path: Path) -> None:
    assert _friction(tmp_path, "cd `pwd`/src && ls")["decision"] == "allow"


# --- Codex review r4 follow-up ------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("< /dev/null install file .smtw/ledger.json", ".smtw/ledger.json"),
        ("</dev/null install file .smtw/ledger.json", ".smtw/ledger.json"),
        ("0< /dev/null install file .smtw/ledger.json", ".smtw/ledger.json"),
        ("0</dev/null install file .smtw/ledger.json", ".smtw/ledger.json"),
        ("<<<x install file .smtw/ledger.json", ".smtw/ledger.json"),
        ("> own.log install file .smtw/ledger.json", ".smtw/ledger.json"),
        ("2>/dev/null install file .smtw/ledger.json", ".smtw/ledger.json"),
        (">>own.log install -t .smtw/bin file", ".smtw/bin"),
        ("< in.txt tee .smtw/ledger.json", ".smtw/ledger.json"),
        ("< fix.diff patch .smtw/ledger.json", ".smtw/ledger.json"),
        ("cd src && < /dev/null install file ../.smtw/ledger.json", "../.smtw/ledger.json"),
        ("LC_ALL=C install file .smtw/ledger.json", ".smtw/ledger.json"),
        ("A=1 B=2 < /dev/null install file .smtw/ledger.json", ".smtw/ledger.json"),
        ("install file .smtw/ledger.json <> rw.txt", ".smtw/ledger.json"),
        ("install file .smtw/ledger.json 3<>rw.txt", ".smtw/ledger.json"),
        ("install file .smtw/ledger.json <&0", ".smtw/ledger.json"),
        ("install file .smtw/ledger.json 0<&-", ".smtw/ledger.json"),
    ],
)
def test_redirections_and_assignments_before_the_command_name_are_skipped(
    tmp_path: Path, command: str, expected: str
) -> None:
    # A leading redirection or assignment is not the command name.
    assert expected in shell_candidate_paths(command), command
    assert _friction(tmp_path, command)["decision"] == "block", command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("< /dev/null install -m 755 build/tool bin/tool", ("bin/tool",)),
        ("LC_ALL=C install build/tool bin/tool", ("bin/tool",)),
        ("install build/tool bin/tool <&0", ("bin/tool",)),
        ("install build/tool bin/tool <> rw.txt", ("rw.txt", "bin/tool")),
    ],
)
def test_leading_redirections_keep_project_writes_allowed(
    tmp_path: Path, command: str, expected: tuple[str, ...]
) -> None:
    assert shell_candidate_paths(command) == expected
    assert _friction(tmp_path, command)["decision"] == "allow", command


def test_claude_hook_blocks_install_after_a_leading_input_redirection(
    tmp_path: Path,
) -> None:
    # Given: a real project whose state directory exists.
    root = tmp_path / "project"
    root.mkdir()
    config = write_config(root)
    ledger = config.parent / "ledger.json"
    payload = {
        "session_id": "followup-r4-redirect",
        "cwd": str(root),
        "tool_name": "Bash",
        "tool_input": {
            "command": f"< /dev/null install file {config.parent.name}/ledger.json"
        },
    }

    # When: the real PreTool adapter evaluates the command.
    result = HookHarness(root, root, tmp_path / "plugin-data").run(
        "pre_tool_use.py", payload
    )

    # Then: the state-file friction denies it.
    decision = result.output.get("hookSpecificOutput")
    assert isinstance(decision, dict), result.output
    assert decision.get("permissionDecision") == "deny"
    assert "R2-friction" in str(decision.get("permissionDecisionReason"))
    assert not ledger.exists()


@pytest.mark.parametrize(
    "command",
    [
        "cd .smtw && echo x >> ~/notes.txt",
        "cd .fable-lite && echo x >> ~/notes.txt",
        "cd .smtw && echo x >> $HOME/notes.txt",
        "cd .smtw && curl -o ~/x https://example.invalid/x",
        "cd .smtw; install file ~/bin/tool",
    ],
)
def test_home_anchored_writes_after_cd_into_state_are_allowed(
    tmp_path: Path, command: str
) -> None:
    # `~` and `$HOME` do not move with `cd`, so the write stays outside `.smtw`.
    assert _friction(tmp_path, command)["decision"] == "allow", command


@pytest.mark.parametrize(
    "command",
    [
        "cd .smtw && echo x >> ./ledger.json",
        "cd .smtw && echo x >> ledger.json",
        "cd .smtw && install file ledger.json",
    ],
)
def test_relative_writes_after_cd_into_state_stay_blocked(
    tmp_path: Path, command: str
) -> None:
    assert _friction(tmp_path, command)["decision"] == "block", command


@pytest.mark.skipif(os.name != "nt", reason="Git Bash drive paths exist only on Windows")
def test_git_bash_drive_path_into_state_stays_blocked_after_cd(tmp_path: Path) -> None:
    drive, rest = str(tmp_path).replace("\\", "/").split(":", 1)
    target = f"/{drive.lower()}{rest}/.smtw/ledger.json"

    result = _friction(tmp_path, f"cd src && echo x >> {target}")

    assert result["decision"] == "block", target
