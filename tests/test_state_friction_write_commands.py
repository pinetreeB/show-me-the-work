"""State-file friction follow-up.

`.smtw/**` friction missed write commands that R2 does not model (`install`,
`patch`, `curl -o`, `wget -O`), redirections glued to a word (`x>>.smtw/f`) and
relative paths written after `cd .smtw`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
