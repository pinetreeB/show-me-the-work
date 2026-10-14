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
