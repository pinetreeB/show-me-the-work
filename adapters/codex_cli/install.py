from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import sys
from typing import Final, cast


ROOT_TOKEN: Final = "{FABLE_LITE_ROOT}"
EXPECTED_ROOT_TOKENS: Final = 8
EVENT_SCRIPTS: Final = {
    "UserPromptSubmit": "user_prompt_submit.py",
    "PreToolUse": "pre_tool_use.py",
    "PostToolUse": "post_tool_use.py",
    "Stop": "stop.py",
}
POWERSHELL: Final = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install self-locating show-me-the-work hooks into a Codex project.",
    )
    _ = parser.add_argument("--target", required=True, type=Path, help="Target project directory")
    return parser


def _first_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, list) or not value or not isinstance(value[0], dict):
        return None
    return cast(dict[str, object], value[0])


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _posix_command(script: Path) -> str:
    return f"{shlex.quote(Path(sys.executable).as_posix())} {shlex.quote(script.as_posix())}"


def _windows_command(script: Path) -> str:
    executable = _powershell_literal(Path(sys.executable).as_posix())
    script_path = _powershell_literal(script.as_posix())
    return f'{POWERSHELL} -NoProfile -ExecutionPolicy Bypass -Command "& {executable} {script_path}"'


def render_hooks(repo_root: Path) -> str | None:
    template = Path(__file__).with_name("hooks.json").read_text(encoding="utf-8")
    if template.count(ROOT_TOKEN) != EXPECTED_ROOT_TOKENS:
        return None
    try:
        raw: object = json.loads(template)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    manifest = cast(dict[str, object], raw)
    hooks_value = manifest.get("hooks")
    if not isinstance(hooks_value, dict):
        return None
    hooks = cast(dict[str, object], hooks_value)

    for event, filename in EVENT_SCRIPTS.items():
        matcher = _first_object(hooks.get(event))
        command_entry = _first_object(matcher.get("hooks")) if matcher is not None else None
        if command_entry is None:
            return None
        command = command_entry.get("command")
        command_windows = command_entry.get("commandWindows")
        if not isinstance(command, str) or ROOT_TOKEN not in command:
            return None
        if not isinstance(command_windows, str) or ROOT_TOKEN not in command_windows:
            return None
        script = repo_root / "adapters" / "codex_cli" / filename
        command_entry["command"] = _posix_command(script)
        command_entry["commandWindows"] = _windows_command(script)

    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    target_value = getattr(args, "target", None)
    if not isinstance(target_value, Path):
        parser.error("--target must be a project directory")

    repo_root = Path(__file__).resolve().parents[2]
    rendered = render_hooks(repo_root)
    if rendered is None:
        print("show-me-the-work Codex hook template is invalid; installation aborted.", file=sys.stderr)
        return 2

    destination = target_value.resolve() / ".codex" / "hooks.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8", newline="\n") as handle:
            _ = handle.write(rendered)
    except FileExistsError:
        print(f"Refusing to overwrite existing Codex hooks: {destination}", file=sys.stderr)
        return 1

    print(f"Installed show-me-the-work Codex hooks: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
