"""Verify the sealed toy inputs before a separately authorized local smoke."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from .advisory import load_sent_items, read_json
from .judge import add_judge_arguments, local_config_from_args
from .local_judge import LocalJudgeRunError, run_local_judge
from .code_identity import code_sha256


SEALED_D1_SHA256 = 'eeb47f0d036a666d2e86237c2aa5dd8f75164c53658215c90d5ec4e8e5e27e74'
IDS = [f'd1-{index:02}' for index in range(1, 13)]


def verify_smoke_seal(seal_file: Path, packets: Path, scope_file: Path) -> dict:
    """No transport, output directory, or answer may be used before this gate."""
    if hashlib.sha256(seal_file.read_bytes()).hexdigest() != SEALED_D1_SHA256:
        raise ValueError('synthetic seal mismatch')
    root = seal_file.resolve().parent
    if packets.resolve() != root / 'packets' or scope_file.resolve() != root / 'scope.json':
        raise ValueError('smoke inputs must belong to the sealed fixture')
    seal = read_json(seal_file)
    for name, expected in seal['files'].items():
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('sealed input resolves outside its fixture')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('sealed fixture data mismatch')
    manifest = read_json(packets / 'manifest.json')
    if (seal['items'] != IDS or len(manifest['packets']) != 12
            or [entry['item_ids'] for entry in manifest['packets']] != [[key] for key in IDS]):
        raise ValueError('smoke requires the twelve sealed one-item packets')
    if list(load_sent_items(packets)) != IDS:
        raise ValueError('sealed smoke item IDs changed')
    return {'seal_sha256': SEALED_D1_SHA256, 'item_ids': IDS,
            'files_verified': len(seal['files']), 'root': root}


def summarize_smoke(run: dict, answers: dict, seal: dict, output_root: Path) -> dict:
    expected = {item['id']: item['expected']['verdict'] for item in answers['items']}
    results = {}
    counts = {'quote_not_in_excerpt': 0, 'missing_quote_or_ref': 0,
              'binding_ambiguous_capped': 0}
    folder = Path(run['run'])
    for path in sorted(folder.glob('judgments-*.json')):
        rows = read_json(path)
        verified_path = folder / path.name.replace('judgments-', 'verified-')
        if not verified_path.is_file():
            continue
        verified = read_json(verified_path)
        displayed = {row['id']: row['verdict'] for row in verified['items']}
        normalization = {row['id']: row['reason_code']
                         for row in verified.get('normalization', [])}
        for key in counts:
            counts[key] += verified['counts'].get(key, 0)
        for row in rows:
            results[row['id']] = {'normalized_verdict': row['verdict'],
                                  'verified_verdict': displayed.get(row['id'], 'not_enough_evidence'),
                                  'reason_code': normalization.get(row['id'])}
    comparisons = []
    for key in IDS:
        result = results.get(key, {'normalized_verdict': 'not_evaluated',
                                   'verified_verdict': 'not_evaluated', 'reason_code': None})
        comparisons.append({'id': key, 'expected': expected[key], **result,
                            'matches_expected': result['verified_verdict'] == expected[key]})
    summary = {'version': 1, 'purpose': 'synthetic plumbing smoke; not quality qualification',
               'seal_sha256': seal['seal_sha256'], 'input_count': 12,
               'code_sha256': code_sha256(), 'all_item_ids': IDS,
               'completed_item_ids': [key for key in IDS if key in results],
               'not_evaluated_item_ids': [key for key in IDS if key not in results],
               'remaining_item_ids': [key for key in IDS if key not in results],
               'evaluated_count': len(results), 'not_evaluated_count': 12 - len(results),
               'expected_matches': sum(row['matches_expected'] for row in comparisons),
               'local_error_count': int(bool(run.get('error'))), **counts,
               'comparison': comparisons, 'run': str(folder)}
    for key in ('error', 'stage', 'http_status', 'server_computation_ended',
                'next_request', 'operator_notice'):
        if key in run:
            summary[key] = run[key]
    path = folder / 'smoke-summary.json'
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return {**summary, 'summary_file': str(path)}


def main(argv=None, *, opener=None, clock=time.monotonic) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_judge_arguments(parser)
    parser.add_argument('--seal', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.provider != 'local' or args.max_calls != 12:
            raise ValueError('smoke requires local provider and exactly twelve calls')
        seal = verify_smoke_seal(args.seal, args.packets, args.scope_file)
        config = local_config_from_args(args)
        config.validate_d2_runtime()
        if args.local_stop_file is None or args.local_server_start_args_sha256 is None:
            raise ValueError('smoke requires stop-file and server-owner startup SHA')
        options = {'scope_file': args.scope_file, 'output_root': args.out_root,
                   'config': config, 'max_calls': 12, 'clock': clock,
                   'blocklist_file': args.blocklist_file,
                   'stop_file': args.local_stop_file,
                   'server_start_args_sha256': args.local_server_start_args_sha256}
        from .local_http import LocalHttpTransport
        options['transport'] = LocalHttpTransport(
            opener=opener, clock=clock,
            stop_requested=lambda: args.local_stop_file.exists())
        failed = False
        try:
            result = run_local_judge(args.packets, **options)
        except LocalJudgeRunError as exc:
            result, failed = exc.result, True
        # Answers are read only for offline comparison, never placed in a request.
        summary = summarize_smoke(result, read_json(seal['root'] / 'answers.json'), seal, args.out_root)
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 2 if failed else 0
    except (OSError, ValueError, KeyError, TypeError):
        print('Local smoke refused; verify the sealed inputs, explicit settings, and safe receipts.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
