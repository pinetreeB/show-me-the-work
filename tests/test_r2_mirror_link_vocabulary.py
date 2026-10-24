"""R2 vocabulary long tail: rsync / robocopy mirroring and forced ln.

These were recorded as known limitations (KL5): `rsync --delete` and
`robocopy /MIR` empty a destination tree, plain recursive mirroring overwrites
it, and `ln -f` replaces whatever file sits at the link name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.destructive_guard import parse_destructive_commands
from r2_peer_support import r2_gate


@pytest.mark.parametrize(
    "command",
    [
        "rsync -a --delete tmp/ src/",
        "rsync -a tmp/ src/",
        "rsync --remove-source-files peer.py backup/",
        "robocopy tmp src /MIR",
        "robocopy src elsewhere /MOVE",
        "ln -sf other.py peer.py",
        "ln --force -s other.py peer.py",
        "ln -sfn other.py peer.py",
        "cd src && rsync -a --delete ../tmp/ .",
    ],
)
def test_mirror_and_forced_link_reach_peer_files(tmp_path: Path, command: str) -> None:
    assert r2_gate(tmp_path, command)["decision"] == "block", command


@pytest.mark.parametrize(
    "command",
    [
        "rsync -a --delete tmp/ mine/",
        "rsync -av src/ host:/srv/app/",
        "rsync -n --delete tmp/ src/",
        "robocopy tmp mine /MIR",
        "robocopy tmp src /L",
        "ln -s other.py peer.py",
        "ln -sf other.py my_link.py",
    ],
)
def test_mirror_and_link_on_own_or_safe_targets_are_allowed(
    tmp_path: Path, command: str
) -> None:
    assert r2_gate(tmp_path, command)["decision"] == "allow", command


def test_rsync_delete_into_project_root_is_implicit_scope(tmp_path: Path) -> None:
    result = r2_gate(tmp_path, "rsync -a --delete tmp/ .")

    assert result["decision"] == "block"


def test_single_operand_forced_link_targets_the_basename() -> None:
    parsed = parse_destructive_commands("ln -sf ../shared/peer.py")

    assert [item.targets for item in parsed] == [("peer.py",)]


@pytest.mark.parametrize(
    "command",
    ['rsync -e"nc relay" --delete tmp/ src/', "rsync -ae nc --delete tmp/ src/"],
)
def test_rsync_value_letters_are_not_read_as_dry_run(tmp_path: Path, command: str) -> None:
    assert r2_gate(tmp_path, command)["decision"] == "block", command


# --- Codex review r1 follow-up: remote and unknown rsync sources ------------

SRC_PEER_ONLY = frozenset({"src/peer.py"})


@pytest.mark.parametrize(
    "command",
    [
        "rsync -a host:src/ src/",
        "rsync -a user@host:/srv/app/ src/",
        "rsync -a host::module/src/ src/",
        "rsync -a rsync://host/module/src/ src/",
        "rsync host:backup/peer.py src/",
        "rsync -a host: src/",
        "rsync -a 'host:src/' src/",
        "rsync -a tmp/* src/",
        "rsync -a ./ src/",
        "rsync --files-from=list.txt tmp src/",
        "rsync --files-from list.txt host:tmp src/",
    ],
)
def test_rsync_remote_or_unknown_source_overwrites_local_destination(
    tmp_path: Path, command: str
) -> None:
    result = r2_gate(tmp_path, command, peer_paths=SRC_PEER_ONLY)

    assert result["decision"] == "block", command


@pytest.mark.parametrize(
    "command",
    [
        "rsync -a host:src/ mine/",
        "rsync -a host:src src",
        "rsync host:backup/other.py src/",
        "rsync -an host:src/ src/",
        "rsync -a src/ host:src/",
    ],
)
def test_rsync_remote_source_into_unrelated_paths_is_allowed(
    tmp_path: Path, command: str
) -> None:
    result = r2_gate(tmp_path, command, peer_paths=SRC_PEER_ONLY)

    assert result["decision"] == "allow", command


def test_rsync_remote_source_name_is_placed_under_the_destination() -> None:
    parsed = parse_destructive_commands("rsync host:dir/a.txt dest/")

    assert [item.targets for item in parsed] == [("dest/", "dest/a.txt")]


# --- Codex review r2 follow-up: value-taking rsync options ------------------


@pytest.mark.parametrize(
    "command",
    [
        "rsync -a --out-format --dry-run --delete tmp/ src/",
        "rsync -a --log-format --dry-run --delete tmp/ src/",
        "rsync -a --info --dry-run --delete tmp/ src/",
        "rsync -a --bwlimit --dry-run --delete tmp/ src/",
        "rsync -a --log-file-format --list-only --delete tmp/ src/",
        "rsync -a --iconv --version --delete tmp/ src/",
        "rsync -a -T -n --delete tmp/ src/",
    ],
)
def test_rsync_option_value_spelled_like_dry_run_is_not_a_dry_run(
    tmp_path: Path, command: str
) -> None:
    # popt hands the next word to a value-taking option even when it looks like
    # an option, so `--out-format --dry-run` prints "--dry-run" and deletes.
    result = r2_gate(tmp_path, command)

    assert result["decision"] == "block", command
    assert result["coordination_reason_code"] == "peer_unsettled"


@pytest.mark.parametrize(
    "command",
    [
        "rsync -a --out-format=%n --dry-run --delete tmp/ src/",
        "rsync -a --out-format %n --dry-run --delete tmp/ src/",
        "rsync -a --info=progress2 --dry-run --delete tmp/ src/",
        "rsync -a --info progress2 -n --delete tmp/ src/",
    ],
)
def test_rsync_real_dry_run_after_a_valued_option_is_allowed(
    tmp_path: Path, command: str
) -> None:
    assert r2_gate(tmp_path, command)["decision"] == "allow", command


@pytest.mark.parametrize(
    "command",
    [
        "ln -sf other.py peer.py < /dev/null",
        "ln -sf other.py peer.py </dev/null",
        "rsync -a --delete tmp/ src/ < /dev/null",
        "rsync -a tmp/ src/ </dev/null",
    ],
)
def test_input_redirection_does_not_replace_the_mirror_or_link_destination(
    tmp_path: Path, command: str
) -> None:
    result = r2_gate(tmp_path, command)

    assert result["decision"] == "block", command
    assert result["coordination_reason_code"] == "peer_unsettled"


# --- Codex review r4 re-run: forced ln options and unknown sources ---------


@pytest.mark.parametrize(
    "command",
    [
        "ln -sf other/* src/",
        "ln -sf other/*.py src",
        "ln -sf $DIR/peer.py src/",
        "ln -sf -t src other/*",
        "ln --for -s other/peer.py src/peer.py",
        "ln -s --f other/peer.py src/",
        "ln -sf --targ=src other/peer.py",
        "ln -sf --target-directory src other/peer.py",
        "ln -sftsrc other/peer.py",
        "ln -sfT other.py src/peer.py",
        "ln -sb other.py src/peer.py",
        "ln -s --backup other.py src/peer.py",
        "ln -si other.py src/peer.py",
        "ln -s other.py src/peer.py --force",
        "ln -sf -S --help other.py src/peer.py",
    ],
)
def test_forced_link_options_and_unknown_sources_reach_peer_files(
    tmp_path: Path, command: str
) -> None:
    result = r2_gate(tmp_path, command, peer_paths=SRC_PEER_ONLY)

    assert result["decision"] == "block", command
    assert result["coordination_reason_code"] == "peer_unsettled"


@pytest.mark.parametrize(
    "command",
    [
        "ln -s other/* src/",
        "ln -sf other/* mine/",
        "ln -sS.tmp other.py src/peer.py",
        "ln -s --suffix=.f other.py src/peer.py",
        "ln -sf other/peer.py mine/",
        "ln --help -f other.py src/peer.py",
    ],
)
def test_link_without_replacement_or_into_own_paths_is_allowed(
    tmp_path: Path, command: str
) -> None:
    # `-S` takes the rest of the bundle as its suffix, so its `f` is no force.
    result = r2_gate(tmp_path, command, peer_paths=SRC_PEER_ONLY)

    assert result["decision"] == "allow", command


def test_forced_link_with_unknown_source_checks_the_whole_directory() -> None:
    parsed = parse_destructive_commands("ln -sf other/* src/")

    assert [(item.targets, item.container_targets) for item in parsed] == [
        (("src/",), ("src/",))
    ]


@pytest.mark.parametrize(
    "command",
    [
        "rsync --remove-source-files --files-from=list.txt src mine/",
        "rsync --remove-source-files --files-from list.txt src mine/",
        "rsync --files-from=list.txt --remove-source-files src/ mine/",
    ],
)
def test_rsync_file_list_removes_files_inside_the_source(
    tmp_path: Path, command: str
) -> None:
    # The list names files inside `src`; they are deleted without `-r`.
    result = r2_gate(tmp_path, command, peer_paths=SRC_PEER_ONLY)

    assert result["decision"] == "block", command
    assert result["coordination_reason_code"] == "peer_unsettled"


def test_rsync_file_list_from_an_own_directory_is_allowed(tmp_path: Path) -> None:
    command = "rsync --remove-source-files --files-from=list.txt mine/ other/"

    assert r2_gate(tmp_path, command, peer_paths=SRC_PEER_ONLY)["decision"] == "allow"
