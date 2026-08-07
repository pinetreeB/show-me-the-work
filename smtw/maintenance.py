"""smtw maintenance plan|apply — ledger lifecycle maintenance CLI (W4a, W5)."""
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
        help="safe-auto/manual-only/legacy-statusless 후보와 source digest를 write-free로 진단합니다.",
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
    apply.add_argument(
        "--retire-legacy-statusless",
        action="store_true",
        help=(
            "status 없는 구 스키마 invocation을 가진 턴을 '증거 부족 퇴역'으로 옮깁니다. "
            "status를 완료로 위조하지 않고 원문을 archive에 보관합니다. "
            "--ack-unverified 필수(연산자 명시 승인)."
        ),
    )
    apply.add_argument(
        "--ack-unverified",
        type=int,
        default=None,
        help="퇴역시킬 status-less invocation 수를 정확히 지정합니다. plan 값과 다르면 거부합니다.",
    )
    apply.add_argument(
        "--source-digest",
        default=None,
        help="예상 source digest. 지정하면 현재 ledger digest와 대조해 불일치 시 거부합니다.",
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
        "legacy_statusless": [_candidate_dict(c) for c in plan.legacy_statusless],
        "statusless_invocations": plan.statusless_invocations,
        "estimated_bytes": plan.estimated_bytes,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Source digest: {payload['source_digest']}")
        print(f"Source bytes: {payload['source_bytes']}")
        print(f"Blocked: {', '.join(payload['blocked']) or 'none'}")
        _print_group("Safe auto", payload["safe_auto"])
        _print_group("Manual only", payload["manual_only"])
        _print_group("Legacy status-less", payload["legacy_statusless"], show_statusless=True)
        print(f"Status-less invocations: {payload['statusless_invocations']}")
        print(f"Estimated bytes: {payload['estimated_bytes']}")
        if plan.legacy_statusless:
            print(
                "\nTo retire the status-less legacy turns (originals are archived, never "
                "marked completed):\n"
                f"  smtw maintenance apply --retire-legacy-statusless "
                f"--ack-unverified {payload['statusless_invocations']} "
                f"--source-digest {payload['source_digest']}"
            )
    return 1 if plan.blocked else 0


def run_maintenance_apply(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    try:
        plan = plan_maintenance(str(root))
        expected = getattr(args, "source_digest", None)
        if expected and expected != plan.source_digest:
            _print_error(
                f"error: source digest mismatch (expected {expected}, found {plan.source_digest})",
                as_json=args.json,
            )
            return 1
        result = apply_maintenance_plan(
            str(root),
            plan,
            include_manual_only=args.include_manual_only,
            retire_legacy_statusless=args.retire_legacy_statusless,
            ack_unverified=args.ack_unverified,
        )
    except LedgerMaintenanceError as exc:
        _print_error(f"error: {exc}", as_json=args.json)
        return 1
    payload = {
        "rejected": result.rejected,
        "reason": result.reason,
        "removed": result.removed,
        "retired": result.retired,
        "backup_path": result.backup_path,
        "archive_path": result.archive_path,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Result: {'REJECTED' if result.rejected else 'OK'}")
        print(f"Reason: {result.reason or 'none'}")
        print(f"Removed: {result.removed}")
        print(f"Retired (unverified legacy): {result.retired}")
        print(f"Backup: {result.backup_path or 'none'}")
        print(f"Archive: {result.archive_path or 'none'}")
    return 1 if result.rejected else 0


def _print_group(
    title: str,
    candidates: list[dict[str, object]],
    *,
    show_statusless: bool = False,
) -> None:
    print(f"{title}: {len(candidates)}")
    for candidate in candidates:
        detail = f"{candidate['reason']}, {candidate['estimated_bytes']}B"
        if show_statusless:
            detail += f", status-less={candidate['statusless_invocations']}"
            exclusions = candidate.get("exclusions") or []
            if exclusions:
                detail += f", would-be-excluded={'/'.join(exclusions)}"
        print(f"  - {candidate['agent_key']} ({detail})")


def _candidate_dict(candidate: object) -> dict[str, object]:
    return {
        "agent_key": candidate.agent_key,
        "turn_id": candidate.turn_id,
        "category": candidate.category,
        "reason": candidate.reason,
        "estimated_bytes": candidate.estimated_bytes,
        "statusless_invocations": candidate.statusless_invocations,
        "exclusions": list(candidate.exclusions),
    }


def _print_error(message: str, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": message}, ensure_ascii=False, sort_keys=True))
    else:
        print(message)
