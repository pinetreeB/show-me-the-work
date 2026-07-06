from __future__ import annotations

import argparse

from .brief import run_brief
from .check import run_check


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fable_lite")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="오케스트레이터 사후 게이트를 실행합니다.")
    check.add_argument("--root", required=True)
    check.add_argument("--agent")
    check.add_argument("--since-file")
    check.set_defaults(func=run_check)

    brief = subparsers.add_parser("brief", help="위임 프롬프트 규율 블록을 생성합니다.")
    brief.add_argument("--paths", required=True)
    brief.add_argument("--verify-cmd", required=True)
    brief.add_argument("--sentinel", required=True)
    brief.add_argument("--target", choices=("codex", "claude", "agy"), default="codex")
    brief.set_defaults(func=run_brief)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))
