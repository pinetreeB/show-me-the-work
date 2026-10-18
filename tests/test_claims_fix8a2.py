from __future__ import annotations

import pytest

from smtw.claims.check import run
from tests.claims_support import agy_call, make_case, view
from tests.test_claims_fix8a import atoms, inspect


def test_mismatched_ordered_quote_count_checks_every_candidate(tmp_path):
    result = inspect(tmp_path, '`sample.md:1, 2` 에 각각 “alpha sample text”·“beta sample text”·“invented sample text”',
                     'alpha sample text\nbeta sample text\n', 'sample.md')
    quotes = atoms(result, 'K5')
    assert len(quotes) == 3
    assert quotes[-1]['verdict'] == 'mismatch'
    assert quotes[-1]['reason_code'] == 'quote_mismatch'
    assert all(c['verdict'] in {'match', 'unverifiable'} for c in quotes[:2])


@pytest.mark.parametrize('condition,expected', [('10', 'match'), ('99', 'mismatch')])
def test_adjacent_candidates_can_support_one_code_quote(tmp_path, condition, expected):
    result = inspect(tmp_path, f'`logic.py:1`, `logic.py:2` `if value > {condition}: return "too large"`',
                     'if value > 10:\n    return "too large"\n', 'logic.py')
    claim, = atoms(result, 'K5')
    assert claim['verdict'] == expected
    assert any(row['range'] == [1, 2] for row in claim['source']['matrix'])


def test_nonadjacent_candidates_do_not_bridge_missing_lines(tmp_path):
    result = inspect(tmp_path, '`logic.py:1`, `logic.py:3` `if value > 10: return "too large"`',
                     'if value > 10:\n    validate(value)\n    return "too large"\n', 'logic.py')
    claim, = atoms(result, 'K5')
    assert claim['verdict'] == 'mismatch'
    assert all(row['range'] != [1, 3] for row in claim['source']['matrix'])


@pytest.mark.parametrize('accurate', [True, False])
def test_long_line_nested_quotes_and_repeated_reads(tmp_path, accurate):
    quotes = ["if state == 'warm' ~ flags:", "return 'ready' ~ stable"]
    long_line = 'padding ' * 60 + ' | '.join(quotes) + ' tail ' * 20
    quoted = quotes if accurate else ["if state == 'invented' ~ flags:", "return 'invented' ~ rejected"]
    report = ('근거: `trace.jsonl:20`…`trace.jsonl:22`…'
              f'(체크포인트 `checkpoint.md:45` 실측: `{quoted[0]}`, `{quoted[1]}`)')
    rows = [agy_call('view_file', AbsolutePath='C:/synthetic/trace.jsonl'),
            view('C:/synthetic/trace.jsonl', 'entry\n' * 30, start=1, end=2)]
    for _ in range(2):
        rows.extend([agy_call('view_file', AbsolutePath='C:/synthetic/checkpoint.md'),
                     view('C:/synthetic/checkpoint.md', 'before\n' * 44 + long_line + '\n')])
    report_path, transcript = make_case(tmp_path, report, rows)
    result = run(report_path, agy=str(transcript))
    selected = [c for c in atoms(result, 'K1') if 'checkpoint.md:45' in c['text']]
    assert selected
    if accurate:
        assert all(c['verdict'] != 'mismatch' for c in selected)
    else:
        assert any(c['verdict'] == 'mismatch' for c in selected)


@pytest.mark.parametrize('verb', ['날조', '지어냄', 'fabricated'])
def test_trailing_source_reference_does_not_own_actual_measurement(tmp_path, verb):
    report = f'3차(`AUDIT-ENTRY`): 줄 L48(실제 174), 스크립트 L1-L248(실제 200줄) {verb}(`source.md:33`).'
    result = inspect(tmp_path, report, 'source entry\n' * 40, 'source.md')
    metrics = atoms(result, 'K2')
    assert any('200' in c['text'] for c in metrics)
    assert all(c['verdict'] == 'unverifiable' for c in metrics)
    assert all(c['reason_code'] in {'inherited_binding', 'reported_claim'} for c in metrics)


def test_trailing_source_without_attribution_does_not_own_measurement(tmp_path):
    result = inspect(tmp_path, '스크립트는 실제 200줄이다(출처 `source.md:3`).', 'one\ntwo\nthree\n', 'source.md')
    claim, = atoms(result, 'K2')
    assert claim['verdict'] == 'unverifiable'
    assert claim['reason_code'] == 'inherited_binding'


def test_explicit_target_wrong_actual_measurement_still_fails(tmp_path):
    result = inspect(tmp_path, '`sample.md`는 실제 200줄이다.', 'one\ntwo\n', 'sample.md')
    claim, = atoms(result, 'K2')
    assert claim['verdict'] == 'mismatch'


def test_identifier_suffix_is_not_a_byte_measurement(tmp_path):
    result = inspect(tmp_path, '3차(`AUDIT-CAMERA-1B`): `sample.md` 파일 크기를 `964 bytes`로 측정했다.',
                     'one\n', 'sample.md')
    metrics = atoms(result, 'K2')
    assert len(metrics) == 1
    assert '964' in metrics[0]['text']
    assert metrics[0]['verdict'] == 'mismatch'


def test_standalone_one_byte_is_still_checked(tmp_path):
    result = inspect(tmp_path, '`sample.md` 파일 크기는 1B이다.', 'long enough\n', 'sample.md')
    claim, = atoms(result, 'K2')
    assert claim['verdict'] == 'mismatch'


def test_one_supported_candidate_prevents_long_line_k1_mismatch(tmp_path):
    result = inspect(tmp_path, "`sample.md:1` 실측: `value = 'present' ~ flags`, `value = 'other' ~ flags`",
                     "padding " * 90 + "value = 'present' ~ flags\n", 'sample.md')
    claims = atoms(result, 'K1')
    assert claims and all(c['verdict'] == 'match' for c in claims)
