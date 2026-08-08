from __future__ import annotations

import argparse
import json
from pathlib import Path

from adapters.claude_code.project_config import load_project_config
from core.ledger_migration import (
    LedgerMigrationError,
    inspect_v2_invocation_statuses,
    migrate_v2_invocation_statuses,
)
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
    parser.add_argument(
        "--invocation-statuses",
        action="store_true",
        help=(
            "invocation status backfill을 명시적으로 실행합니다. --check와 함께면 "
            "write-free 진단(required/safe/unsafe)만 수행합니다."
        ),
    )
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=run_migrate)


def run_migrate(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    if args.invocation_statuses:
        return _run_invocation_status_migration(root, args)
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
    payload["next_step"] = _post_migration_next_step(root, payload)
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
        if payload["next_step"]:
            print(f"Next step: {payload['next_step']}")
    return result.exit_code


def _post_migration_next_step(root: Path, payload: dict[str, object]) -> str:
    """MIGRATION-04: 활성화가 아직 legacy config에 매달려 있으면 알린다.

    `config.json`은 transient로 분류돼 이전되지 않는다(state_layout.py). 그래서
    마이그레이션 뒤에도 활성화는 legacy 디렉터리에 남은 파일에 의존한다. 이 상태로
    legacy를 지우면 **감독이 조용히 꺼진다** — 상태는 healthy로 보이고 게이트만
    사라지므로 알아채기 어렵다. 2026-08-08 실측으로 확인된 함정이다.
    """
    if payload.get("result") not in {"MIGRATED", "ALREADY_MIGRATED"}:
        return ""
    try:
        config = load_project_config(root)
    except Exception:  # noqa: BLE001 — 진단 보조이므로 실패해도 마이그레이션을 막지 않는다
        return ""
    if config.source is not None and config.source.value != "legacy":
        return ""
    return (
        "smtw init  (activation still resolves from the legacy config; "
        "deleting the legacy directory before this turns supervision off)"
    )


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


def _run_invocation_status_migration(
    root: Path,
    args: argparse.Namespace,
) -> int:
    """W1: ``smtw migrate --invocation-statuses [--check]``.

    --check는 write-free 진단(required/safe/unsafe/source digest)만 수행한다.
    apply는 unsafe row(증거 없는 status-less)가 있으면 절대 자동 close하지 않고
    exit 1 + 원본/archive 무변경으로 중단한다. 기존 lease-aware backfill을
    재사용한다(core.ledger_migration).
    """
    try:
        inspection = inspect_v2_invocation_statuses(str(root))
    except LedgerMigrationError as exc:
        payload = {
            "check": bool(args.check),
            "invocation_statuses": True,
            "result": "ERROR",
            "detail": str(exc),
        }
        _print_invocation_status(payload, as_json=args.json)
        return 1

    if args.check:
        payload = {
            "check": True,
            "invocation_statuses": True,
            **inspection,
            "result": _invocation_render_result(inspection),
            "changed": False,
        }
        _print_invocation_status(payload, as_json=args.json)
        return 1 if inspection["unsafe"] > 0 else 0

    if inspection["ledger"] != "v2":
        payload = {
            "check": False,
            "invocation_statuses": True,
            **inspection,
            "result": "NOT_APPLICABLE",
            "changed": False,
        }
        _print_invocation_status(payload, as_json=args.json)
        return 1
    if inspection["unsafe"] > 0:
        payload = {
            "check": False,
            "invocation_statuses": True,
            **inspection,
            "result": "BLOCKED",
            "changed": False,
        }
        _print_invocation_status(payload, as_json=args.json)
        return 1
    try:
        _ = migrate_v2_invocation_statuses(str(root))
    except LedgerMigrationError as exc:
        payload = {
            "check": False,
            "invocation_statuses": True,
            "result": "ERROR",
            "detail": str(exc),
        }
        _print_invocation_status(payload, as_json=args.json)
        return 1
    payload = {
        "check": False,
        "invocation_statuses": True,
        **inspection,
        "unsafe": 0,
        "result": "OK",
        "changed": bool(inspection["required"]),
    }
    _print_invocation_status(payload, as_json=args.json)
    return 0


def _invocation_render_result(inspection: dict[str, object]) -> str:
    if inspection["ledger"] in {"absent", "not_v2"}:
        return "NOT_APPLICABLE"
    if inspection["unsafe"] > 0:
        return "UNSAFE"
    return "OK"


def _print_invocation_status(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    print(f"Result: {payload['result']}")
    if "detail" in payload:
        print(f"Detail: {payload['detail']}")
    if "ledger" in payload:
        print(f"Ledger: {payload['ledger']}")
        print(f"Required: {payload['required']}")
        print(f"Safe: {payload['safe']}")
        print(f"Unsafe: {payload['unsafe']}")
        print(f"Source digest: {payload['source_digest']}")
        print(f"Source bytes: {payload['source_bytes']}")
    if "changed" in payload:
        print(f"Changed: {str(payload['changed']).lower()}")
