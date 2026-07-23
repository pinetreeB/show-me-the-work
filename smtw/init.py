from __future__ import annotations

import argparse
import json
from pathlib import Path
import tomllib

from adapters.claude_code.project_config import (
    ConfigLoadState,
    ConfigSource,
    load_project_config,
)
from core.project_root import is_user_home_root
from core.state_layout import LEGACY_STATE_DIR_NAME, STATE_DIR_NAME


_CONFIG_TEXT = "schema_version = 1\nsupervision = true\n"
_PYPROJECT_TEXT = "[tool.smtw]\nschema_version = 1\nsupervision = true\n"
_GITIGNORE_PATTERNS = (
    f"/{STATE_DIR_NAME}/",
    f"/{LEGACY_STATE_DIR_NAME}/",
    "/.smtw.migrating-*",
    "/.smtw-migration.lock",
    "/.smtw-migration-receipt.json",
)


def add_init_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "init",
        help="initialize explicit SMTW project configuration",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--config",
        choices=("dedicated", "pyproject"),
        default="dedicated",
    )
    parser.add_argument("--no-gitignore", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=run_init)


def run_init(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    payload, exit_code = _initialize(
        root,
        config_target=args.config,
        update_gitignore=not args.no_gitignore,
    )
    payload["exit_code"] = exit_code
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        _print_human(payload)
    return exit_code


def _initialize(
    root: Path,
    *,
    config_target: str,
    update_gitignore: bool,
) -> tuple[dict[str, object], int]:
    base: dict[str, object] = {
        "project_root": str(root),
        "config": "none",
        "gitignore": "unchanged",
        "next_step": "Run: smtw doctor --root " + _quote_arg(str(root)),
    }
    if not root.is_dir():
        return {
            **base,
            "result": "invalid_root",
            "detail": "project root must be an existing directory",
        }, 1
    if is_user_home_root(root):
        return {
            **base,
            "result": "exact_home_refused",
            "detail": "SMTW cannot initialize the exact user home as a project",
        }, 2

    loaded = load_project_config(root)
    if loaded.state is ConfigLoadState.DECLARED_INVALID:
        return {
            **base,
            "result": "invalid_existing_config",
            "config": loaded.source.value if loaded.source else "unknown",
            "detail": loaded.detail,
        }, 1
    if loaded.state is ConfigLoadState.VALID:
        if loaded.source is ConfigSource.LEGACY:
            return {
                **base,
                "result": "legacy_config_detected",
                "config": "legacy",
                "detail": "legacy config was preserved; no automatic migration ran",
                "next_step": (
                    "Run: smtw migrate --check --root "
                    + _quote_arg(str(root))
                    + "; then choose smtw migrate explicitly"
                ),
            }, 2
        return {
            **base,
            "result": "already_configured",
            "config": loaded.source.value if loaded.source else "unknown",
            "detail": "existing canonical configuration was preserved",
        }, 0

    try:
        if config_target == "pyproject":
            config_path = _append_pyproject(root)
        else:
            config_path = _create_dedicated(root)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        return {
            **base,
            "result": "write_failed",
            "detail": f"{type(exc).__name__}: {exc}",
        }, 1

    gitignore_status = "skipped"
    if update_gitignore:
        try:
            gitignore_status = _update_gitignore(root / ".gitignore")
        except OSError as exc:
            return {
                **base,
                "result": "gitignore_failed",
                "config": str(config_path),
                "detail": f"{type(exc).__name__}: {exc}",
            }, 1
    return {
        **base,
        "result": "initialized",
        "config": str(config_path),
        "gitignore": gitignore_status,
        "detail": "configuration created; runtime state was not created",
    }, 0


def _create_dedicated(root: Path) -> Path:
    path = root / ".smtw.toml"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        _ = handle.write(_CONFIG_TEXT)
    return path


def _append_pyproject(root: Path) -> Path:
    path = root / "pyproject.toml"
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    if original:
        _ = tomllib.loads(original)
    separator = "" if not original or original.endswith("\n\n") else (
        "\n" if original.endswith("\n") else "\n\n"
    )
    with path.open("x" if not path.exists() else "a", encoding="utf-8", newline="\n") as handle:
        _ = handle.write(separator + _PYPROJECT_TEXT)
    return path


def _update_gitignore(path: Path) -> str:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    existing = {line.strip() for line in original.splitlines()}
    missing = [pattern for pattern in _GITIGNORE_PATTERNS if pattern not in existing]
    if not missing:
        return "unchanged"
    prefix = "" if not original or original.endswith("\n") else "\n"
    with path.open("x" if not path.exists() else "a", encoding="utf-8", newline="\n") as handle:
        _ = handle.write(prefix + "\n".join(missing) + "\n")
    return "updated"


def _quote_arg(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def _print_human(payload: dict[str, object]) -> None:
    print(f"Project root: {payload['project_root']}")
    print(f"Result: {payload['result']}")
    print(f"Config: {payload['config']}")
    print(f"Gitignore: {payload['gitignore']}")
    print(f"Detail: {payload.get('detail', '')}")
    print(f"Next step: {payload['next_step']}")
