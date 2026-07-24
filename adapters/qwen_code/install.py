from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from typing import Final, cast


HOOK_COMMAND_TOKEN: Final = "{SMTW_HOOK_COMMAND}"
EXPECTED_HOOK_COMMAND_TOKENS: Final = 6
EVENT_NAMES: Final = (
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SessionStart",
    "SessionEnd",
)
OWNED_FRAGMENT: Final = "/adapters/qwen_code/qwen_hook.py"
TRUST_FOLDER: Final = "TRUST_FOLDER"
TRUSTED_FOLDERS_FILENAME: Final = "trustedFolders.json"
TRUSTED_FOLDERS_ENV: Final = "QWEN_CODE_TRUSTED_FOLDERS_PATH"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install self-locating show-me-the-work hooks for qwen-code.",
    )
    _ = parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help=(
            "Target project directory. Installs into <target>/.qwen/settings.json and "
            "registers the folder as TRUST_FOLDER. Omit for a user-scope install into "
            "~/.qwen/settings.json (no trust registration needed)."
        ),
    )
    _ = parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Atomically replace only smtw-owned hook entries in existing settings",
    )
    return parser


def _first_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, list) or not value or not isinstance(value[0], dict):
        return None
    return cast(dict[str, object], value[0])


def _windows_env_value(path: Path) -> str:
    # qwen-code는 Windows에서 훅을 cmd.exe /d /s /c로 실행한다. command 문자열에
    # embedded 따옴표를 넣으면 Node의 \" 이스케이프를 cmd가 해석 못 해 spawn이
    # 실패한다(실증). 대신 command에는 %VAR%만 두고 env 값에 따옴표를 포함하면
    # cmd가 %VAR% 확장 시 따옴표까지 복원해 공백 경로도 안전하다.
    return '"' + path.as_posix() + '"'


def _posix_command_prefix(python: Path, script: Path) -> str:
    return f"{shlex.quote(python.as_posix())} {shlex.quote(script.as_posix())}"


def render_hooks(repo_root: Path) -> str | None:
    template = Path(__file__).with_name("hooks.json").read_text(encoding="utf-8")
    if template.count(HOOK_COMMAND_TOKEN) != EXPECTED_HOOK_COMMAND_TOKENS:
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

    python = Path(sys.executable)
    script = repo_root / "adapters" / "qwen_code" / "qwen_hook.py"
    is_windows = os.name == "nt"
    prefix = "%SMTW_PYTHON% %SMTW_HOOK%" if is_windows else _posix_command_prefix(python, script)

    for event in EVENT_NAMES:
        matcher = _first_object(hooks.get(event))
        command_entry = _first_object(matcher.get("hooks")) if matcher is not None else None
        if command_entry is None:
            return None
        command = command_entry.get("command")
        if not isinstance(command, str) or HOOK_COMMAND_TOKEN not in command:
            return None
        command_entry["command"] = command.replace(HOOK_COMMAND_TOKEN, prefix)
        if is_windows:
            command_entry["env"] = {
                "SMTW_PYTHON": _windows_env_value(python),
                "SMTW_HOOK": _windows_env_value(script),
            }

    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def _is_owned_hook(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    entry = cast(dict[str, object], value)
    command = entry.get("command")
    if isinstance(command, str) and OWNED_FRAGMENT in command.replace("\\", "/"):
        return True
    env = entry.get("env")
    if isinstance(env, dict):
        hook_path = env.get("SMTW_HOOK")
        if isinstance(hook_path, str) and OWNED_FRAGMENT in hook_path.replace("\\", "/"):
            return True
    return False


def _has_owned_hooks(hooks: dict[str, object]) -> bool:
    for event in EVENT_NAMES:
        matchers = hooks.get(event)
        if not isinstance(matchers, list):
            continue
        for raw_matcher in matchers:
            if not isinstance(raw_matcher, dict):
                continue
            entries = cast(dict[str, object], raw_matcher).get("hooks")
            if isinstance(entries, list) and any(_is_owned_hook(entry) for entry in entries):
                return True
    return False


def merge_owned_hooks(
    existing: dict[str, object],
    rendered: dict[str, object],
) -> dict[str, object] | None:
    existing_hooks_value = existing.get("hooks", {})
    rendered_hooks_value = rendered.get("hooks")
    if not isinstance(existing_hooks_value, dict) or not isinstance(rendered_hooks_value, dict):
        return None
    existing_hooks = cast(dict[str, object], existing_hooks_value)
    rendered_hooks = cast(dict[str, object], rendered_hooks_value)
    existing["hooks"] = existing_hooks

    for event in EVENT_NAMES:
        current_value = existing_hooks.get(event, [])
        replacement_value = rendered_hooks.get(event)
        if not isinstance(current_value, list) or not isinstance(replacement_value, list):
            return None
        replacement_matcher = _first_object(replacement_value)
        if replacement_matcher is None:
            return None

        retained: list[object] = []
        for raw_matcher in current_value:
            if not isinstance(raw_matcher, dict):
                retained.append(raw_matcher)
                continue
            matcher = cast(dict[str, object], raw_matcher)
            hooks_value = matcher.get("hooks")
            if not isinstance(hooks_value, list):
                retained.append(matcher)
                continue
            filtered = [entry for entry in hooks_value if not _is_owned_hook(entry)]
            if len(filtered) == len(hooks_value):
                retained.append(matcher)
            elif filtered:
                retained.append({**matcher, "hooks": filtered})

        existing_hooks[event] = [*retained, replacement_matcher]
    return existing


def _parsed_object(text: str) -> dict[str, object] | None:
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    return cast(dict[str, object], raw) if isinstance(raw, dict) else None


def _atomic_replace_text(destination: Path, content: str) -> None:
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=destination.parent,
        prefix=f"{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            _ = handle.write(content)
        os.replace(temporary, destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _trusted_folders_path() -> Path:
    override = os.environ.get(TRUSTED_FOLDERS_ENV)
    if override:
        return Path(override)
    return Path.home() / ".qwen" / TRUSTED_FOLDERS_FILENAME


def _register_trust(target: Path) -> str | None:
    """Returns an error message on failure, None on success."""
    trust_path = _trusted_folders_path()
    data: dict[str, object] = {}
    if trust_path.exists():
        try:
            parsed = _parsed_object(trust_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            return f"Cannot read trusted folders file: {exc}"
        if parsed is None:
            return f"Trusted folders file is not a JSON object: {trust_path}"
        data = parsed
    data[str(target)] = TRUST_FOLDER
    try:
        trust_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_replace_text(trust_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    except OSError as exc:
        return f"Cannot write trusted folders file: {exc}"
    return None


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    target_value = getattr(args, "target", None)
    if target_value is not None and not isinstance(target_value, Path):
        parser.error("--target must be a project directory")

    repo_root = Path(__file__).resolve().parents[2]
    rendered = render_hooks(repo_root)
    if rendered is None:
        print("show-me-the-work qwen-code hook template is invalid; installation aborted.", file=sys.stderr)
        return 2
    replacement = _parsed_object(rendered)
    if replacement is None:
        print("show-me-the-work qwen-code hook template is invalid; installation aborted.", file=sys.stderr)
        return 2

    upgrade = getattr(args, "upgrade", False) is True
    if isinstance(target_value, Path):
        destination = target_value.resolve() / ".qwen" / "settings.json"
        scope_label = "workspace"
    else:
        destination = Path.home() / ".qwen" / "settings.json"
        scope_label = "user"

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            _atomic_replace_text(destination, rendered)
        else:
            try:
                existing_text = destination.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                print(f"Cannot read existing qwen settings: {exc}", file=sys.stderr)
                return 1
            existing = _parsed_object(existing_text)
            if existing is None:
                print(f"Existing qwen settings are not a JSON object: {destination}", file=sys.stderr)
                return 1
            existing_hooks = existing.get("hooks", {})
            if isinstance(existing_hooks, dict) and _has_owned_hooks(existing_hooks) and not upgrade:
                print(
                    f"smtw-owned qwen hooks already installed: {destination} (use --upgrade to replace)",
                    file=sys.stderr,
                )
                return 1
            merged = merge_owned_hooks(existing, replacement)
            if merged is None:
                print(f"Existing qwen settings have an incompatible hooks shape: {destination}", file=sys.stderr)
                return 1
            _atomic_replace_text(destination, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    except OSError as exc:
        print(f"Cannot write qwen settings: {exc}", file=sys.stderr)
        return 1

    if isinstance(target_value, Path):
        error = _register_trust(target_value.resolve())
        if error is not None:
            print(error, file=sys.stderr)
            return 1
        print(f"Installed show-me-the-work qwen hooks ({scope_label}, trust registered): {destination}")
        return 0

    print(f"Installed show-me-the-work qwen hooks ({scope_label}): {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
