"""Opt-in offline assist commands; deliberately contains no model runner."""
from __future__ import annotations

import json
import math

from .assist import select_candidates
from .check import run
from .code_identity import code_sha256
from .paths import mapping
from .model import Budget
from .privacy import load_blocklist, needs_blocklist
from .judge_scope import load_scope, work_source_basis
from .packets import build_packets, fixed_sources, transcript_sources, write_packets
from .transcripts import discover_agy, resolve_agy


def check_inputs(args):
    if not math.isfinite(args.time_limit) or args.time_limit <= 0:
        raise ValueError("--time-limit은 양의 유한 초 단위 숫자여야 합니다.")
    result = run(args.report, agy=args.agy, agy_auto=args.agy_auto, codex=args.codex,
               brain_root=args.agy_brain_root, repos=args.repo, commits=mapping(args.commit),
               sources=mapping(args.source_file, basename_default=True),
               default_file=mapping([args.default_file]) if args.default_file is not None else None,
               aliases=mapping(args.path_alias), time_limit=args.time_limit)
    result["code_sha256"] = code_sha256()
    return result


def run_assist(args) -> int:
    try:
        local_candidates = bool(getattr(args, "local_candidates", False))
        case_id = getattr(args, "case_id", None)
        scope_file = getattr(args, "scope_file", None)
        scope = None
        if args.dry_run or case_id is not None or scope_file is not None:
            if not case_id or scope_file is None:
                raise ValueError("--dry-run 또는 범위 지정에는 --case-id와 --scope-file이 함께 필요합니다.")
            scope = load_scope(scope_file)
            if case_id not in scope.cases:
                raise ValueError("--case-id는 --scope-file의 cases에 등록된 키여야 합니다.")
        blocklist = (load_blocklist(getattr(args, 'blocklist_file', None), required=needs_blocklist(scope))
                     if args.dry_run else None)
        if args.verify:
            from .advisory import render_advisory, render_with_advisory, verify_files
            if args.packets is None:
                raise ValueError("--verify에는 --packets가 필요합니다.")
            if scope is not None:
                from .advisory import load_sent_items, read_json, verify_judgments
                from .judge import validate_scope
                manifest = read_json(args.packets / "manifest.json")
                if manifest.get("case_id") != case_id:
                    raise ValueError("패킷 manifest의 case_id가 --case-id와 다릅니다.")
                sent = load_sent_items(args.packets)
                validate_scope(manifest, sent, scope)
                advisory = verify_judgments(read_json(args.verify), sent)
            else:
                advisory = verify_files(args.verify, args.packets)
            if args.report is not None:
                checked = check_inputs(args)
                output = (json.dumps({"checker": checked, "assist": advisory}, ensure_ascii=False, indent=2)
                          if args.format == "json" else render_with_advisory(checked, advisory))
                print(output)
                return {"PASS": 0, "FAIL": 1, "ERROR": 2, "INCOMPLETE": 3}[checked["status"]]
            print(json.dumps(advisory, ensure_ascii=False, indent=2) if args.format == "json"
                  else render_advisory(advisory))
            return 0
        if args.report is None:
            raise ValueError("선별/dry-run에는 --report가 필요합니다.")
        checked = check_inputs(args)
        result = select_candidates(args.report.read_text(encoding="utf-8-sig"), checked,
                                   limit=args.limit, fixed_commit=bool(args.commit),
                                   local_candidates=local_candidates)
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
            source_stats = {'skipped_hard_exclude': 0}
            if agy:
                sources = transcript_sources(resolve_agy(agy, args.agy_brain_root), "agy",
                                             args.report, text, aliases, budget, source_stats)
            elif args.codex:
                sources = transcript_sources(args.codex, "codex", args.report, text, aliases, budget, source_stats)
            sources += fixed_sources(result["items"], mapping(args.commit), aliases, args.report, budget, source_stats)
            from .judge_eval import allowed_source
            scoped_sources = []
            for source in sources:
                basis = work_source_basis(case_id, source.path, scope, report=text)
                # Work documents must come from a captured read, never git/worktree.
                external = work_source_basis(case_id, source.path, scope)
                if external:
                    if basis and source.kind == "transcript_read":
                        source.scope_basis = basis
                        scoped_sources.append(source)
                elif allowed_source(case_id, source.path, scope):
                    scoped_sources.append(source)
            packets, summary = build_packets(result, scoped_sources, report=args.report, aliases=aliases,
                                             blocklist=blocklist, local_candidates=local_candidates)
            summary.update(case_id=case_id, scope_sha256=scope.sha256,
                           skipped_out_of_scope=len(sources) - len(scoped_sources), **source_stats)
            if local_candidates:
                summary["local_selection"] = True
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
            print(json.dumps({key: result[key] for key in ("branch_counts", "selected_branch_counts")}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
