from __future__ import annotations

import argparse
import json

from core.state_migration import migrate_state
from core.state_layout import LEGACY_STATE_DIR_NAME, STATE_DIR_NAME


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
    parser.set_defaults(func=run_migrate)


def run_migrate(args: argparse.Namespace) -> int:
    result = migrate_state(
        args.root,
        activation=True,
        lock_wait_seconds=args.lock_wait_seconds,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return result.exit_code
