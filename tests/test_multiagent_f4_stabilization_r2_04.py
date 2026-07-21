from __future__ import annotations

from pathlib import Path

import pytest

from core.destructive_guard import evaluate_r2_destructive_gate
from core.ledger import JsonObject


def _payload(root: Path, command: str) -> JsonObject:
    return {
        "project_root": str(root),
        "tool_name": "Bash",
        "command": command,
        "host": "codex_cli",
        "agent": "codex",
        "session_id": "r2-symlink-state",
    }


def _evaluate(root: Path, command: str) -> JsonObject:
    return evaluate_r2_destructive_gate(
        _payload(root, command),
        lookup_path_attribution=lambda _ledger, _canonical: None,
        attribution_health=lambda _ledger: {
            "degraded": False,
            "capacity_exceeded": False,
        },
    )


def _symlink_state_dir(root: Path, external: Path) -> None:
    root.mkdir()
    external.mkdir()
    try:
        (root / ".fable-lite").symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")


def test_r2_blocks_ledger_delete_under_symlinked_state_dir(tmp_path: Path) -> None:
    root = tmp_path / "project"
    external = tmp_path / "external-state"
    _symlink_state_dir(root, external)

    result = _evaluate(root, "rm .fable-lite/ledger.json")

    assert result["decision"] == "block"
    assert "state_dir_protected" in str(result["reason"])


@pytest.mark.parametrize(
    "target",
    [
        "./.fable-lite/ledger.json",
        "src/../.fable-lite/ledger.json",
    ],
)
def test_r2_blocks_normalized_lexical_paths_to_symlinked_state_dir(
    tmp_path: Path,
    target: str,
) -> None:
    root = tmp_path / "project"
    external = tmp_path / "external-state"
    _symlink_state_dir(root, external)

    result = _evaluate(root, f"rm {target}")

    assert result["decision"] == "block"
    assert "state_dir_protected" in str(result["reason"])


def test_r2_does_not_block_state_dir_prefix_lookalike(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    result = _evaluate(root, "rm .fable-lite-backup/ledger.json")

    assert result["decision"] == "allow"


def test_r2_keeps_ordinary_out_of_root_target_out_of_scope(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    result = _evaluate(root, "rm ../ordinary-outside.txt")

    assert result["decision"] == "allow"
