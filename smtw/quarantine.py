from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from core.quarantine import (
    QuarantineRecord,
    clear_entries,
    list_entries,
    load_entry,
)


_TRUNCATION_WARNING = (
    "WARNING: Blocked content was only partially preserved; "
    "do not apply as a complete command."
)
_UNKNOWN_COMPLETENESS_WARNING = (
    "WARNING: Quarantine completeness metadata is unavailable; "
    "do not apply as a complete command."
)


def add_quarantine_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    quarantine = subparsers.add_parser(
        "quarantine", help="R2에 차단된 명령의 로컬 백업을 조회/회수합니다."
    )
    quarantine_subparsers = quarantine.add_subparsers(
        dest="quarantine_command", required=True
    )

    list_parser = quarantine_subparsers.add_parser(
        "list", help="보관된 항목(시각·agent·대상·크기)을 나열합니다."
    )
    list_parser.add_argument("--root", default=".")
    list_parser.set_defaults(func=run_quarantine)

    show_parser = quarantine_subparsers.add_parser(
        "show", help="보관된 항목과 불완전성 경고를 출력합니다."
    )
    show_parser.add_argument("id")
    show_parser.add_argument("--root", default=".")
    show_parser.set_defaults(func=run_quarantine)

    clear_parser = quarantine_subparsers.add_parser(
        "clear", help="항목을 정리합니다(개별 id 또는 --all)."
    )
    clear_parser.add_argument("id", nargs="?", default=None)
    clear_parser.add_argument("--all", action="store_true")
    clear_parser.add_argument("--root", default=".")
    clear_parser.set_defaults(func=run_quarantine)
    # 자동 apply는 의도적으로 비범위(B1 Q-5) -- list/show/clear까지만 노출한다.


def _record_to_json(record: QuarantineRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "created_at": record.created_at,
        "agent_key": record.agent_key,
        "reason_code": record.reason_code,
        "target": record.target,
        "size_bytes": record.size_bytes,
        "original_bytes": record.original_bytes,
        "stored_bytes": record.stored_bytes,
        "original_sha256": record.original_sha256,
        "stored_sha256": record.stored_sha256,
        "truncated": record.truncated,
        "encoding": record.encoding,
        "record_status": record.record_status,
    }


def run_quarantine(args: argparse.Namespace) -> int:
    command = str(args.quarantine_command)
    root = str(Path(str(args.root)).resolve())
    if command == "list":
        records = list_entries(root)
        print(json.dumps([_record_to_json(r) for r in records], ensure_ascii=False))
        return 0
    if command == "show":
        loaded = load_entry(root, str(args.id))
        if loaded is None:
            print(
                json.dumps({"error": "not_found", "id": str(args.id)}, ensure_ascii=False),
                file=sys.stderr,
            )
            return 1
        record, content = loaded
        if record.truncated is True or record.record_status == "incomplete":
            print(_TRUNCATION_WARNING)
        elif record.truncated is None or record.record_status == "unknown":
            print(_UNKNOWN_COMPLETENESS_WARNING)
        print(content, end="")
        return 0
    if command == "clear":
        entry_id = args.id
        clear_all = bool(args.all)
        if not clear_all and not entry_id:
            print(
                json.dumps({"error": "id_or_all_required"}, ensure_ascii=False),
                file=sys.stderr,
            )
            return 2
        removed = clear_entries(
            root,
            entry_id=str(entry_id) if entry_id else None,
            clear_all=clear_all,
        )
        print(json.dumps({"removed": removed}, ensure_ascii=False))
        return 0
    return 2
