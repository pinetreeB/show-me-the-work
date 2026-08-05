from __future__ import annotations

from pathlib import Path

import pytest

from core.destructive_guard import evaluate_r2_destructive_gate
from core.ledger import JsonObject, record_event


def _identity(root: Path, agent: str) -> JsonObject:
    return {
        "project_root": str(root),
        "host": "host",
        "session_id": f"session-{agent}",
        "agent": agent,
        "turn_id": f"turn-{agent}",
        "attribution": "exact",
    }


def _seed_open_peer(root: Path, path: str) -> None:
    peer = _identity(root, "beta")
    _ = record_event(peer | {"event": "prompt", "prompt": "beta work"})
    _ = record_event(
        peer
        | {
            "event": "invocation",
            "invocation_id": "beta-edit",
            "candidate_paths": [path],
        }
    )


def _decision(root: Path, command: str) -> str:
    result = evaluate_r2_destructive_gate(
        _identity(root, "alpha") | {"tool_name": "Bash", "command": command}
    )
    return str(result["decision"])


@pytest.mark.parametrize(
    "command",
    (
        "rm -r shared-dir",
        "rm -rf shared-dir",
        "rd /s shared-dir",
        "rd /S /Q shared-dir",
        "rmdir /s shared-dir",
        "Remove-Item -Recurse shared-dir",
        "mv shared-dir moved-dir",
        "Move-Item shared-dir moved-dir",
        "Rename-Item shared-dir renamed-dir",
        "git checkout -- shared-dir",
        "git restore -- shared-dir",
        "git rm -r shared-dir",
        "cp -tshared-dir peer.py",
        "mv -tshared-dir peer.py",
        "cp --target-directory=shared-dir peer.py",
    ),
)
def test_container_target_blocks_open_peer_descendant(
    tmp_path: Path,
    command: str,
) -> None:
    _seed_open_peer(tmp_path, "shared-dir/peer.py")

    assert _decision(tmp_path, command) == "block"


def test_container_target_blocks_persisted_unsettled_peer_descendant(
    tmp_path: Path,
) -> None:
    peer = _identity(tmp_path, "beta")
    _ = record_event(peer | {"event": "prompt", "prompt": "beta work"})
    _ = record_event(
        peer
        | {
            "event": "change",
            "event_id": "change-beta-1",
            "seq": 1,
            "manifest_generation": 1,
            "commit_state": "committed",
            "owner": "beta",
            "paths": [
                {
                    "change_id": "change-id-beta-1",
                    "path": "shared-dir/peer.py",
                    "kind": "code",
                    "before": "blake2b-256:before",
                    "after": "blake2b-256:after",
                    "requires_verification": True,
                }
            ],
        }
    )

    assert _decision(tmp_path, "rm -r shared-dir") == "block"


@pytest.mark.parametrize(
    "command",
    (
        "cp -R mine-dir shared-dir",
        "Copy-Item -Recurse mine-dir shared-dir",
    ),
)
def test_recursive_copy_blocks_only_derived_destination_scope(
    tmp_path: Path,
    command: str,
) -> None:
    _seed_open_peer(tmp_path, "shared-dir/mine-dir/peer.py")

    assert _decision(tmp_path, command) == "block"


@pytest.mark.parametrize(
    "command",
    (
        "cp -R mine-dir shared-dir",
        "Copy-Item -Recurse mine-dir shared-dir",
        "cp mine.py shared-dir",
        "rm -r my_own_dir",
        "rd /s my_own_dir",
        "rmdir /s /q my_own_dir",
        "rd shared-dir",
        "Remove-Item -Recurse my_own_dir",
        "mv my_own_dir renamed_own",
        "ls -la shared-dir",
        "grep -r TODO shared-dir",
        "cp -tmy_own_dir mine.py",
    ),
)
def test_unrelated_peer_descendant_does_not_block_container_command(
    tmp_path: Path,
    command: str,
) -> None:
    _seed_open_peer(tmp_path, "shared-dir/other.py")

    assert _decision(tmp_path, command) == "allow"


def test_container_prefix_match_respects_path_boundary(tmp_path: Path) -> None:
    _seed_open_peer(tmp_path, "shared-directory/peer.py")

    assert _decision(tmp_path, "rm -r shared-dir") == "allow"
