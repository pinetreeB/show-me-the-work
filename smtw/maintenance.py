"""smtw maintenance plan|apply — ledger lifecycle maintenance CLI (W4a)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.ledger_maintenance import (
    LedgerMaintenanceError,
    apply_maintenance_plan,
    plan_maintenance,
)


def add_maintenance_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "maintenance",
        help="ledger lifecycle maintenance (plan/apply). doctor에는 repair를 넣지 않습니다.",
    )
    sub = parser.add_subparsers(dest="maintenance_command", required=True)

    plan = sub.add_parser(
        "plan",
        help="safe-auto/manual-only 정리 후보와 source digest를 write-free로 진단합니다.",
    )
    plan.add_argument("--root", default=".")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=run_maintenance_plan)

    apply = sub.add_parser(
        "apply",
        help="digest CAS 재확인 후 exact 후보만 정리합니다. unsafe/manual-only는 명시 선택 시에만.",
    )
    apply.add_argument("--root", default=".")
    apply.add_argument(
        "--include-manual-only",
        action="store_true",
        help="timestamp-less legacy turn도 적용에 포함합니다(연산자 명시 선택).",
    )
    apply.add_argument("--json", action="store_true")
    apply.set_defaults(func=run_maintenance_apply)


def run_maintenance_plan(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    try:
        plan = plan_maintenance(str(root))
    except LedgerMaintenanceError as exc:
        _print_error(f"error: {exc}", as_json=args.json)
        return 1
    payload = {
        "source_digest": plan.source_digest,
        "source_bytes": plan.source_bytes,
        "blocked": list(plan.blocked),
        "safe_auto": [_candidate_dict(c) for c in plan.safe_auto],
        "manual_only": [_candidate_dict(c) for c in plan.manual_only],
        "estimated_bytes": plan.estimated_bytes,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Source digest: {payload['source_digest']}")
        print(f"Source bytes: {payload['source_bytes']}")
        print(f"Blocked: {', '.join(payload['blocked']) or 'none'}")
        print(f"Safe auto: {len(payload['safe_auto'])}")
        for candidate in payload["safe_auto"]:
            print(f"  - {candidate['agent_key']} ({candidate['reason']}, {candidate['estimated_bytes']}B)")
        print(f"Manual only: {len(payload['manual_only'])}")
        for candidate in payload["manual_only"]:
            print(f"  - {candidate['agent_key']} ({candidate['reason']}, {candidate['estimated_bytes']}B)")
        print(f"Estimated bytes: {payload['estimated_bytes']}")
    return 1 if plan.blocked else 0


def run_maintenance_apply(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    try:
        plan = plan_maintenance(str(root))
        result = apply_maintenance_plan(
            str(root),
            plan,
            include_manual_only=args.include_manual_only,
        )
    except LedgerMaintenanceError as exc:
        _print_error(f"error: {exc}", as_json=args.json)
        return 1
    payload = {
        "rejected": result.rejected,
        "reason": result.reason,
        "removed": result.removed,
        "backup_path": result.backup_path,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Result: {'REJECTED' if result.rejected else 'OK'}")
        print(f"Reason: {result.reason or 'none'}")
        print(f"Removed: {result.removed}")
        print(f"Backup: {result.backup_path or 'none'}")
    return 1 if result.rejected else 0


def _candidate_dict(candidate: object) -> dict[str, object]:
    return {
        "agent_key": candidate.agent_key,
        "turn_id": candidate.turn_id,
        "category": candidate.category,
        "reason": candidate.reason,
        "estimated_bytes": candidate.estimated_bytes,
    }


def _print_error(message: str, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": message}, ensure_ascii=False, sort_keys=True))
    else:
        print(message)