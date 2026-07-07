from __future__ import annotations

from pathlib import Path


def launcher_path(hook_file: str) -> Path:
    return Path(hook_file).resolve().parents[2] / "fable-lite-cli.py"


def intent_set_command(hook_file: str) -> str:
    return f'python "{launcher_path(hook_file)}" intent set --root . --goal "..." --scope "..." [--non-goal "..."]'
