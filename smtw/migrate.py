from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.state_migration import check_migration_state, migrate_state
from core.state_layout import (
    LEGACY_STATE_DIR_NAME,
    STATE_DIR_NAME,
    LayoutInspection,
    StateLayout,
    inspect_state_layout_details,
)


def add_migrate_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "migrate",
        help=(
            f"legacy {LEGACY_STATE_DIR_NAME} 상태를 검증 후 "
            f"{STATE_DIR_NAME}로 명시적으로 복사합니다."
        ),
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--lock-wait-seconds", type=float, default=15.0)
    parser.add_argument(
        "--check",
        action="store_true",
        help="assess readiness without locks or writes",
    )
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=run_migrate)


def run_migrate(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    before = inspect_state_layout_details(root)
    active_turn = _active_turn_count(before)
    if args.check:
        result = check_migration_state(root, activation=True)
    else:
        result = migrate_state(
            root,
            activation=True,
            lock_wait_seconds=args.lock_wait_seconds,
        )
    payload = {
        **result.as_dict(),
        "current_layout": before.layout.value,
        "active_turn": active_turn,
        "files": result.file_count,
        "bytes": result.total_bytes,
        "result": result.status.value.upper(),
        "authority": _authority_name(root),
        "legacy_retained": (
            LEGACY_STATE_DIR_NAME
            if (root / LEGACY_STATE_DIR_NAME).is_dir()
            else "none"
        ),
        "check": bool(args.check),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Current layout: {payload['current_layout']}")
        print(f"Active turn: {payload['active_turn']}")
        print(f"Files: {payload['files']}")
        print(f"Bytes: {payload['bytes']}")
        print(f"Result: {payload['result']}")
        print(f"Authority: {payload['authority']}")
        print(f"Legacy retained: {payload['legacy_retained']}")
    return result.exit_code


def _active_turn_count(inspection: LayoutInspection) -> str:
    layout = inspection.layout
    if layout in {StateLayout.LEGACY, StateLayout.MIGRATING}:
        authority = inspection.legacy
    else:
        authority = inspection.target
    try:
        raw: object = json.loads((authority / "ledger.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return "none"
    if not isinstance(raw, dict):
        return "unknown"
    turns = raw.get("active_turns")
    if not isinstance(turns, dict) or not turns:
        return "none"
    return str(len(turns))


def _authority_name(root: Path) -> str:
    inspection = inspect_state_layout_details(root)
    if inspection.layout in {
        StateLayout.EMPTY,
        StateLayout.NATIVE,
        StateLayout.MIGRATED,
    }:
        return STATE_DIR_NAME
    if inspection.layout in {StateLayout.LEGACY, StateLayout.MIGRATING}:
        return LEGACY_STATE_DIR_NAME
    return "none"
