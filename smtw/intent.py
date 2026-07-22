from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.intent import IntentInput, clear_intent, load_intent, save_intent
from core.ledger import load_ledger
from core.state_layout import STATE_DIR_NAME


def add_intent_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    intent = subparsers.add_parser("intent", help="모호한 수정 의도를 기록하거나 조회합니다.")
    intent_subparsers = intent.add_subparsers(dest="intent_command", required=True)

    set_parser = intent_subparsers.add_parser(
        "set",
        help=f"확정된 의도를 선택된 상태 트리(새 프로젝트: {STATE_DIR_NAME})의 intent.json에 저장합니다.",
    )
    set_parser.add_argument("--root", default=".")
    set_parser.add_argument("--goal", required=True)
    set_parser.add_argument("--scope", default="")
    set_parser.add_argument("--non-goal", action="append", default=[])
    set_parser.add_argument("--assumed", action="store_true")
    set_parser.add_argument("--confirmed-at-prompt", default="")
    set_parser.add_argument("--ambiguity-score", type=int)
    set_parser.set_defaults(func=run_intent)

    show_parser = intent_subparsers.add_parser("show", help="현재 intent.json을 출력합니다.")
    show_parser.add_argument("--root", default=".")
    show_parser.set_defaults(func=run_intent)

    clear_parser = intent_subparsers.add_parser("clear", help="현재 intent.json을 삭제합니다.")
    clear_parser.add_argument("--root", default=".")
    clear_parser.set_defaults(func=run_intent)


def run_intent(args: argparse.Namespace) -> int:
    command = str(args.intent_command)
    root = str(Path(str(args.root)).resolve())
    if command == "set":
        score_value = args.ambiguity_score
        score = score_value if isinstance(score_value, int) else _ledger_score(root)
        record = save_intent(
            root,
            IntentInput(
                goal=str(args.goal),
                scope=_split_scope(str(args.scope)),
                non_goals=[str(item) for item in args.non_goal],
                assumed=bool(args.assumed),
                confirmed_at_prompt=str(args.confirmed_at_prompt),
                ambiguity_score=score,
            ),
        )
        print(json.dumps(record, ensure_ascii=False, sort_keys=True))
        return 0
    if command == "show":
        print(json.dumps(load_intent(root), ensure_ascii=False, sort_keys=True))
        return 0
    if command == "clear":
        clear_intent(root)
        print("{}")
        return 0
    return 2


def _split_scope(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _ledger_score(root: str) -> int:
    value = load_ledger({"project_root": root}).get("ambiguity_score")
    return value if isinstance(value, int) else 0
