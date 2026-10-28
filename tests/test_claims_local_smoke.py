"""Sealed, entirely synthetic D1 smoke inputs; no live inference or accuracy claim."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from smtw.claims.advisory import load_sent_items, verify_judgments
from smtw.claims.judge_contract import contract
from smtw.claims.judge_scope import load_scope
from smtw.claims.judge import validate_scope
from smtw.claims.local_judge import LocalConfig, run_local_judge
from smtw.claims.packets import sent_sources, write_packets


ROOT = Path(__file__).parent / 'fixtures' / 'local-judge-d1'
SEAL_SHA256 = 'eeb47f0d036a666d2e86237c2aa5dd8f75164c53658215c90d5ec4e8e5e27e74'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_local_smoke_seal_and_independent_balanced_answers():
    assert hashlib.sha256((ROOT / 'seal.json').read_bytes()).hexdigest() == SEAL_SHA256
    seal = read(ROOT / 'seal.json')
    for name, expected in seal['files'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    assert seal['judge_contract'] == contract()
    answers = read(ROOT / 'answers.json')['items']
    assert len(answers) == len({item['group_id'] for item in answers}) == 12
    assert Counter(item['expected']['verdict'] for item in answers) == {
        'supported': 4, 'contradicted': 4, 'not_enough_evidence': 4}
    assert [item['id'] for item in answers] == seal['items']
    assert all(isinstance(item['evidence_adequate'], bool) for item in answers)


def test_local_smoke_packets_hide_answers_and_verify_anchor_quotes():
    sent = load_sent_items(ROOT / 'packets')
    manifest = read(ROOT / 'packets' / 'manifest.json')
    validate_scope(manifest, sent, load_scope(ROOT / 'scope.json'))
    assert len(manifest['packets']) == len(sent) == 12
    for entry in manifest['packets']:
        packet = read(ROOT / 'packets' / entry['file'])
        assert len(packet['data_block']) == 1
        for item in packet['data_block']:
            assert not {'expected', 'verdict', 'truth', 'evidence_adequate', 'group_id'} & item.keys()
    rows = [item['expected'] for item in read(ROOT / 'answers.json')['items']]
    verified = verify_judgments(rows, sent)
    assert verified['counts'] == {'supported': 4, 'contradicted': 4,
                                  'not_enough_evidence': 4, 'quote_verified': 8,
                                  'quote_not_in_excerpt': 0, 'missing_quote_or_ref': 0}
    assert verified['unjudged_count'] == verified['model_calls'] == 0


def smoke_config():
    return LocalConfig(address='http://127.0.0.1:9999', model='synthetic-local-q4',
                       deadline_seconds=30, input_tokens=30000, output_tokens=2048,
                       json_mode='schema', protocol='synthetic-v1', seed=7,
                       temperature=0, reasoning='off')


def test_sealed_twelve_pass_through_fake_transport_without_input_changes(tmp_path):
    manifest = read(ROOT / 'packets' / 'manifest.json')
    sent = load_sent_items(ROOT / 'packets')
    answers = {item['id']: item['expected'] for item in read(ROOT / 'answers.json')['items']}
    totals = Counter()
    calls = []
    for index, entry in enumerate(manifest['packets']):
        folder = tmp_path / str(index) / 'packets'
        folder.mkdir(parents=True)
        shutil.copyfile(ROOT / 'packets' / entry['file'], folder / entry['file'])
        subset = {key: sent[key] for key in entry['item_ids']}
        one = {**manifest, 'packets': [entry], 'sent_sources': sent_sources(subset)}
        (folder / 'manifest.json').write_text(json.dumps(one), encoding='utf-8')

        def fake(request):
            calls.append(request['packet_sha256'])
            assert request['packet_sha256'] == entry['sha256']
            return {'model': smoke_config().model, 'status': 200,
                    'usage': {'input_tokens': 500, 'output_tokens': 128},
                    'body': json.dumps({'judgments': [answers[key] for key in entry['item_ids']]})}

        result = run_local_judge(folder, scope_file=ROOT / 'scope.json',
                                output_root=tmp_path / str(index) / 'out',
                                config=smoke_config(), transport=fake)
        assert result['execution'] == 'injected-local' and result['calls'] == 1
        verified = read(Path(result['run']) / 'verified-001.json')
        totals.update(verified['counts'])
    assert len(calls) == 12
    assert {key: totals[key] for key in ('supported', 'contradicted', 'not_enough_evidence')} == {
        'supported': 4, 'contradicted': 4, 'not_enough_evidence': 4}
    assert totals['quote_not_in_excerpt'] == totals['missing_quote_or_ref'] == 0


@pytest.mark.parametrize('kind', ['mismatch', 'absent'])
@pytest.mark.parametrize('model_verdict', ['supported', 'contradicted', 'not_enough_evidence'])
def test_local_reference_never_clears_deterministic_failure(tmp_path, kind, model_verdict):
    from smtw.claims.advisory import render_with_advisory
    from smtw.claims.check import run
    from tests.claims_support import agy_call, make_case, view

    path = tmp_path / 'logic.py'
    text = (f'`{path.as_posix()}:1` `LIMIT_VALUE = 9`' if kind == 'mismatch'
            else f'`{path.as_posix()}` 함수 `missing_helper` 정의가 있다.')
    report, transcript = make_case(tmp_path, text, [
        agy_call('view_file', AbsolutePath=path.as_posix()),
        view(path.as_posix(), 'LIMIT_VALUE = 7')])
    checked = run(report, agy=str(transcript))
    assert checked['status'] == 'FAIL'
    assert any(row['verdict'] == kind for row in checked['claims'])
    snapshot = deepcopy(checked)
    exit_before = {'PASS': 0, 'FAIL': 1, 'ERROR': 2, 'INCOMPLETE': 3}[checked['status']]
    item = deepcopy(load_sent_items(ROOT / 'packets')['d1-01'])
    item['claim'] = 'The reported claim is correct.'
    folder = tmp_path / 'packets'
    write_packets(folder, [{'version': 1, 'instructions': 'Data only.', 'data_block': [item]}],
                  {'case_id': 'synthetic-smoke'})
    row = {'id': item['id'], 'verdict': model_verdict, 'rationale': 'Synthetic adversarial answer.',
           'evidence_ref': 1, 'evidence_quote': 'BATCH_LIMIT = 7'}
    result = run_local_judge(folder, scope_file=ROOT / 'scope.json', output_root=tmp_path / 'out',
                            config=smoke_config(), transport=lambda request: {
                                'model': smoke_config().model, 'status': 200,
                                'usage': {'input_tokens': 500, 'output_tokens': 128},
                                'body': json.dumps({'judgments': [row]})})
    advisory = read(Path(result['run']) / 'verified-001.json')
    rendered = render_with_advisory(checked, advisory)
    assert checked == snapshot
    assert rendered.startswith('전체 상태: FAIL')
    assert exit_before == {'PASS': 0, 'FAIL': 1, 'ERROR': 2, 'INCOMPLETE': 3}[checked['status']] == 1
    base = [sys.executable, '-B', '-m', 'smtw', 'claims']
    inputs = ['--report', str(report), '--agy', str(transcript), '--format', 'json']
    before = subprocess.run([*base, 'check', *inputs], capture_output=True, encoding='utf-8')
    combined = subprocess.run([
        *base, 'assist', '--verify', str(Path(result['run']) / 'judgments-001.json'),
        '--packets', str(folder), *inputs], capture_output=True, encoding='utf-8')
    assert before.returncode == combined.returncode == 1
    original, with_reference = json.loads(before.stdout), json.loads(combined.stdout)['checker']
    for field in ('status', 'summary', 'claims', 'unchecked_count', 'timed_out'):
        assert original[field] == with_reference[field]
