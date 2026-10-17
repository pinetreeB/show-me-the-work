from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from .check import run
from .paths import mapping


def add_claims_parser(subparsers):
    parser = subparsers.add_parser('claims', help='보고서 주장을 읽기 근거와 사후 대조합니다.')
    checks = parser.add_subparsers(dest='claims_command', required=True)
    check = checks.add_parser('check', help='결정론 주장 대조 (모델·API 호출 없음)')
    check.add_argument('--report', type=Path, required=True)
    transcript = check.add_mutually_exclusive_group()
    transcript.add_argument('--agy')
    transcript.add_argument('--agy-auto', action='store_true', help='마지막 보고서 쓰기가 정확히 일치하는 대화 1개만 자동 연결')
    transcript.add_argument('--codex', type=Path)
    check.add_argument('--agy-brain-root', type=Path, default=Path('~/.gemini/antigravity-cli/brain'))
    for option in ('repo', 'commit', 'source-file', 'path-alias'):
        check.add_argument('--' + option, action='append', default=[])
    check.add_argument('--format', choices=('text', 'json'), default='text')
    check.add_argument('--time-limit', type=float, default=60)
    check.set_defaults(func=run_check)


def render_text(result: dict) -> str:
    names = {'match': '일치', 'mismatch': '불일치', 'absent': '출처 없음', 'unverifiable': '미확인', 'range_only': '범위만 확인'}
    output = [f"전체 상태: {result['status']}"]
    if 'error' in result:
        output.append(result['error'])
    for c in result.get('claims', []):
        output.append(f"{c['report_line']}행 [{c['check_type']}] {names[c['verdict']]}: {c['text']}")
        output.append('  ' + c['reason'])
        if c['exempt'] and c['verdict'] != 'match':
            output.append('  표시 주장 검토 필요')
        output.append('  근거·검색 범위: ' + json.dumps(c['source'], ensure_ascii=False))
    summary = result.get('summary', {})
    output.append(' / '.join(f'{names[k]} {v}' for k, v in summary.items()))
    output.append(f"시간 상한: {result.get('timed_out', False)}, 미검사: {result.get('unchecked_count', 0)}")
    for warning in result.get('warnings', []):
        output.append('참고: ' + (warning if isinstance(warning, str) else json.dumps(warning, ensure_ascii=False)))
    return '\n'.join(output)


def run_check(args) -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        if not math.isfinite(args.time_limit) or args.time_limit <= 0:
            raise ValueError('--time-limit은 양의 유한 초 단위 숫자여야 합니다.')
        result = run(args.report, agy=args.agy, agy_auto=args.agy_auto, codex=args.codex, brain_root=args.agy_brain_root,
                     repos=args.repo, commits=mapping(args.commit), sources=mapping(args.source_file, basename_default=True),
                     aliases=mapping(args.path_alias), time_limit=args.time_limit)
    except (OSError, ValueError) as exc:
        result = {'status': 'ERROR', 'claims': [], 'timed_out': False, 'unchecked_count': 0,
                  'author_marked_review': [], 'warnings': [], 'error': f'실행 오류: {exc}'}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.format == 'json' else render_text(result))
    return {'PASS': 0, 'FAIL': 1, 'ERROR': 2, 'INCOMPLETE': 3}[result['status']]
