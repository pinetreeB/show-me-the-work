from __future__ import annotations

from pathlib import Path

import pytest

from core.contract import evaluate_state_file_friction
from core.shell_hints import shell_candidate_paths


@pytest.mark.parametrize("method", ["rename", "replace"])
def test_inline_path_move_hints_include_receiver_and_destination(method: str) -> None:
    command = (
        'python -c "from pathlib import Path; '
        f"Path('tmp/ledger.json').{method}(Path('.fable-lite/ledger.json'))\""
    )

    assert shell_candidate_paths(command) == (
        "tmp/ledger.json",
        ".fable-lite/ledger.json",
    )


@pytest.mark.parametrize(
    "arguments",
    [
        "mode='w'",
        "'r+'",
        "'rb+'",
        "'w+'",
        "'a+'",
        "'x+'",
    ],
)
def test_inline_path_open_hints_include_writable_update_modes(arguments: str) -> None:
    command = (
        "python -c \"from pathlib import Path; "
        f"Path('.fable-lite/ledger.json').open({arguments})\""
    )

    assert shell_candidate_paths(command) == (".fable-lite/ledger.json",)


@pytest.mark.parametrize(
    "call",
    [
        "open('.fable-lite/ledger.json', 'r+')",
        "open(file='.fable-lite/ledger.json', mode='a+')",
        "open('.fable-lite/ledger.json', mode='wb')",
    ],
)
def test_inline_builtin_open_hints_include_writable_modes(call: str) -> None:
    command = f'python -c "{call}"'

    assert shell_candidate_paths(command) == (".fable-lite/ledger.json",)


@pytest.mark.parametrize(
    "source",
    [
        "Path('.fable-lite/x').read_text()",
        "Path('.fable-lite/x').exists()",
        "Path('.fable-lite/x').open('r')",
        "Path('.fable-lite/x').open(mode='rb')",
        "open('.fable-lite/x', 'r')",
        "open(file='.fable-lite/x', mode='rb')",
    ],
)
def test_inline_python_read_operations_are_not_write_hints(source: str) -> None:
    command = f'python -c "from pathlib import Path; {source}"'

    assert shell_candidate_paths(command) == ()


def test_state_file_friction_blocks_inline_path_move_destination(
    tmp_path: Path,
) -> None:
    command = (
        'python -c "from pathlib import Path; '
        "Path('tmp/ledger.json').replace(Path('.fable-lite/ledger.json'))\""
    )

    result = evaluate_state_file_friction(
        {
            "project_root": str(tmp_path),
            "tool_name": "Bash",
            "command": command,
        }
    )

    assert result["decision"] == "block"
    assert "마찰 장치" in str(result["reason"])
