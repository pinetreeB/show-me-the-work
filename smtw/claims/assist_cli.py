"""Opt-in offline assist commands; deliberately contains no model runner."""
from __future__ import annotations

import json
import math

from .assist import select_candidates
from .check import run
from .paths import mapping
from .model import Budget
from .packets import build_packets, fixed_sources, transcript_sources, write_packets
from .transcripts import discover_agy, resolve_agy


def check_inputs(args):
    if not math.isfinite(args.time_limit) or args.time_limit <= 0:
        raise ValueError("--time-limit은 양의 유한 초 단위 숫자여야 합니다.")
    return run(args.report, agy=args.agy, agy_auto=args.agy_auto, codex=args.codex,
               brain_root=args.agy_brain_root, repos=args.repo, commits=mapping(args.commit),
               sources=mapping(args.source_file, basename_default=True),
               default_file=mapping([args.default_file]) if args.default_file is not None else None,
               aliases=mapping(args.path_alias), time_limit=args.time_limit)


def run_assist(args) -> int:
    try:
        checked = check_inputs(args)
        result = select_candidates(args.report.read_text(encoding="utf-8-sig"), checked,
                                   limit=args.limit, fixed_commit=bool(args.commit))
        if args.dry_run:
            if args.out is None:
                raise ValueError("--dry-run에는 --out이 필요합니다.")
            budget = Budget(args.time_limit)
            aliases = mapping(args.path_alias)
            text = args.report.read_text(encoding="utf-8-sig")
            agy = args.agy
            if args.agy_auto:
                found = discover_agy(args.report, text, args.agy_brain_root, aliases, budget)
                if len(found) != 1:
                    raise ValueError("자동 연결 전사를 하나로 확정하지 못했습니다.")
                agy = str(found[0])
            sources = []
            if agy:
                sources = transcript_sources(resolve_agy(agy, args.agy_brain_root), "agy",
                                             args.report, text, aliases, budget)
            elif args.codex:
                sources = transcript_sources(args.codex, "codex", args.report, text, aliases, budget)
            sources += fixed_sources(result["items"], mapping(args.commit), aliases, args.report, budget)
            packets, summary = build_packets(result, sources, report=args.report, aliases=aliases)
            manifest = write_packets(args.out, packets, summary)
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            return 0
        result["checker"] = {k: checked[k] for k in ("status", "summary", "timed_out", "unchecked_count")}
        if args.aggregate:
            result.pop("items")
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"참고 후보 {result['candidate_count']}건 / 선별 {result['selected_count']}건 / 미판정 {result['deferred_count']}건")
            print(json.dumps(result["branch_counts"], ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
