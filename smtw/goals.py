from __future__ import annotations

import argparse
import json

from goals.goals import GoalsCliError, add_identity_arguments, execute


def add_goals_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    goals = subparsers.add_parser(
        "goals", help="identity별 multi-story goals checkpoint를 관리합니다."
    )
    commands = goals.add_subparsers(dest="goals_command", required=True)

    plan = commands.add_parser("plan", help="goals checkpoint를 작성합니다.")
    _ = plan.add_argument("--root", required=True)
    _ = plan.add_argument("--goal", required=True)
    _ = plan.add_argument("--story", required=True)
    _ = plan.add_argument("--verify-cmd", required=True)
    add_identity_arguments(plan)
    plan.set_defaults(func=run_goals)

    verify = commands.add_parser("verify", help="story 검증 증거를 기록합니다.")
    _ = verify.add_argument("--root", required=True)
    _ = verify.add_argument("--story", required=True)
    _ = verify.add_argument("--evidence", required=True)
    add_identity_arguments(verify)
    verify.set_defaults(func=run_goals)

    status = commands.add_parser("status", help="현재 identity의 goals를 조회합니다.")
    _ = status.add_argument("--root", required=True)
    add_identity_arguments(status)
    status.set_defaults(func=run_goals)


def run_goals(args: argparse.Namespace) -> int:
    try:
        result = execute(args, command_field="goals_command")
    except GoalsCliError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI failures must be explicit.
        print(
            json.dumps(
                {"error": "goals_failed", "message": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
