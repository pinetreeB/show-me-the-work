from __future__ import annotations

import argparse

from .brief import run_brief
from .check import run_check
from .doctor import add_diagnostics_parsers
from .goals import add_goals_parser
from .init import add_init_parser
from .intent import add_intent_parser
from .migrate import add_migrate_parser
from .quarantine import add_quarantine_parser
from .scorecard import add_scorecard_parser
from .versioning import package_version

# Compatibility for callers that used to patch this private import. Source
# checkout precedence is now enforced inside smtw.versioning.
from .versioning import _installed_version as _installed_version


def run_version(_args: argparse.Namespace) -> int:
    print(package_version())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smtw")
    subparsers = parser.add_subparsers(dest="command", required=True)

    version_parser = subparsers.add_parser("version", help="설치된 버전을 출력합니다.")
    version_parser.set_defaults(func=run_version)

    check = subparsers.add_parser("check", help="오케스트레이터 사후 게이트를 실행합니다.")
    check.add_argument("--root")
    check.add_argument("--agent")
    check.add_argument("--since-file")
    check.add_argument("--card")
    check.add_argument("--design", action="store_true")
    check.set_defaults(func=run_check)

    brief = subparsers.add_parser("brief", help="위임 프롬프트 규율 블록을 생성합니다.")
    brief.add_argument("--paths")
    brief.add_argument("--verify-cmd")
    brief.add_argument("--sentinel")
    brief.add_argument("--card")
    brief.add_argument("--target", choices=("codex", "claude", "agy"))
    brief.set_defaults(func=run_brief)

    add_intent_parser(subparsers)
    add_goals_parser(subparsers)
    add_scorecard_parser(subparsers)
    add_quarantine_parser(subparsers)
    add_migrate_parser(subparsers)
    add_diagnostics_parsers(subparsers)
    add_init_parser(subparsers)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))
