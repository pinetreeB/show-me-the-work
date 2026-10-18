from __future__ import annotations

import re

import pytest

from smtw.claims.check import run
from smtw.claims.sources import Repositories
from tests.claims_support import agy_call, make_case, view
from tests.test_claims_fix8a import atoms, inspect, repository

NOTES = 'title line\nintro\nalpha line one\nfiller\nfiller2\nfiller3\nbeta line two\nfiller4\nfiller5\nend\n'


@pytest.mark.parametrize('report', [
    '`notes.md:3`, `notes.md:7` 인용: “alpha line TWO”',
    '`notes.md:3`, `notes.md:7` 에는 “alpha line TWO” 가 있다',
    '`notes.md:3`, `notes.md:7` “alpha line TWO” “beta line two”',
])
def test_open_group_missing_literal_still_fails(tmp_path, report):
    result = inspect(tmp_path, report, NOTES)
    assert any(c['verdict'] == 'mismatch' and c['reason_code'] == 'quote_mismatch'
               for c in atoms(result, 'K5'))
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('gap', ['인용:', '에는', '설명상'])
def test_open_group_true_literal_keeps_file_evidence_and_unknown_line_binding(tmp_path, gap):
    result = inspect(tmp_path, f'`notes.md:3`, `notes.md:7` {gap} “beta line two”', NOTES)
    quote, = atoms(result, 'K5')
    assert quote['verdict'] == 'match'
    assert quote['source']['whole_file_open_group'] is True
    assert all(c['reason_code'] == 'binding_ambiguous' for c in atoms(result, 'K1'))
    assert result['status'] == 'INCOMPLETE'


def test_open_code_ellipsis_does_not_disable_literal_absence(tmp_path):
    result = inspect(tmp_path, '`logic.py:1`, `logic.py:2` `if value > 99: ...`',
                     'if value > 10:\n    return True\n', 'logic.py')
    quote, = atoms(result, 'K5')
    assert quote['verdict'] == 'mismatch'
    assert result['status'] == 'FAIL'


def test_open_group_keeps_definite_exceeded_range(tmp_path):
    result = inspect(tmp_path, '`notes.md:3`, `notes.md:900` 인용: “alpha line one”', NOTES)
    assert any(c['reason_code'] == 'range_exceeded' for c in atoms(result, 'K1'))
    assert result['status'] == 'FAIL'


def test_open_group_checks_all_observed_versions(tmp_path):
    rows = []
    for content in [NOTES, NOTES.replace('alpha line one', 'alpha line uno')]:
        rows += [agy_call('view_file', AbsolutePath='C:/synthetic/notes.md'),
                 view('C:/synthetic/notes.md', content)]
    report, transcript = make_case(tmp_path, '`notes.md:3`, `notes.md:7` 인용: “alpha line TWO”', rows)
    result = run(report, agy=str(transcript))
    quote, = atoms(result, 'K5')
    assert quote['verdict'] == 'mismatch'
    assert quote['source']['quote_check']['version_count'] == 2


@pytest.mark.parametrize('report', [
    '`notes.md:3` “beta line two” 및 “alpha line one”',
    '`notes.md:3-4` “alpha line one” 그리고 “beta line two”',
])
def test_multiple_quotes_cannot_turn_wrong_line_binding_into_pass(tmp_path, report):
    result = inspect(tmp_path, report, NOTES)
    assert all(c['verdict'] == 'unverifiable' and c['reason_code'] == 'binding_ambiguous'
               for c in atoms(result, 'K1'))
    assert result['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('report', [
    '엔진 코드는 총 500줄이다 (`engine.py:1-3`).',
    'The engine has 500 lines (see `engine.py:1`).',
    '엔진 파일 크기는 99999 bytes이다 (`engine.py:1`).',
    '엔진 코드는 총 500줄이다(`engine.py:1-3`)',
])
def test_sole_source_file_still_owns_its_wrong_measurement(tmp_path, report):
    result = inspect(tmp_path, report, 'one\ntwo\nthree\n', 'engine.py')
    assert any(c['verdict'] == 'mismatch' for c in atoms(result, 'K2'))
    assert result['status'] == 'FAIL'


def test_sole_source_correct_measurement_still_matches(tmp_path):
    result = inspect(tmp_path, '엔진 코드는 총 3줄이다 (`engine.py:1-3`).',
                     'one\ntwo\nthree\n', 'engine.py')
    assert all(c['verdict'] == 'match' for c in atoms(result, 'K2'))
    assert result['status'] == 'PASS'


@pytest.mark.parametrize('adjective', ['대략적으로', '일반적', '구체적', '정적'])
def test_adjective_is_not_an_attribution_verb(tmp_path, adjective):
    result = inspect(tmp_path, f'이 파일은 {adjective} `engine.py` 총 500줄이지만 문제 없다.',
                     'one\ntwo\nthree\n', 'engine.py')
    assert any(c['verdict'] == 'mismatch' for c in atoms(result, 'K2'))
    assert all(c['reason_code'] != 'reported_claim' for c in atoms(result, 'K2'))


@pytest.mark.parametrize('quote,expected', [('alpha line one', 'match'), ('beta line two', 'match'),
                                           ('alpha line TWO', 'mismatch')])
def test_parenthesized_quote_uses_all_preceding_candidates(tmp_path, quote, expected):
    result = inspect(tmp_path, f'`notes.md:3, 7` (“{quote}” 설명)', NOTES)
    claim, = atoms(result, 'K5')
    assert claim['verdict'] == expected
    assert claim['source']['candidate_ranges'] == [['notes.md', 3, 3], ['notes.md', 7, 7]]


@pytest.mark.parametrize('path', ['a.patch', 'b.html', 'c.rst', 'd.json', 'e.ipynb',
                                  'f.diff', 'LICENSE', 'g.yaml', 'docs/sample.py',
                                  'reviews/sample.js'])
def test_identifier_hit_guard_independently_rejects_noncode_paths(path):
    token = 'ghost_sample_fn'
    assert Repositories._identifier_hit(path, 1, 'def ghost_sample_fn():',
                                       token, 'a' * 40, re.compile(token),
                                       re.compile(r'def ' + token), '') is None


def test_identifier_hit_guard_accepts_real_source():
    token = 'actual_sample_fn'
    hit = Repositories._identifier_hit('src/sample.py', 1, 'def actual_sample_fn():',
                                      token, 'a' * 40, re.compile(token),
                                      re.compile(r'def ' + token), '.py')
    assert hit and hit['kind'] == 'definition'


def test_doc_directory_with_source_extension_cannot_rescue_missing_name(tmp_path):
    repo, revision = repository(tmp_path, {'docs/sample.py': 'def ghost_sample_fn(): pass\n'})
    result = inspect(tmp_path, '`notes.md`에 함수 `ghost_sample_fn`이 정의돼 있다.', NOTES,
                     repos=[str(repo)], commits={str(repo): revision})
    claim, = atoms(result, 'K4')
    assert claim['verdict'] == 'absent'

def test_repository_json_key_does_not_rescue_an_unrelated_missing_identifier(tmp_path):
    repo, revision = repository(tmp_path, {'config.json': '{"realKey": 2}\n'})
    result = inspect(tmp_path, '`notes.md` 설정 키 `realKey: 2`가 있다.', NOTES,
                     repos=[str(repo)], commits={str(repo): revision})
    claims = [c for c in result['claims'] if c['check_type'] in {'K4', 'K6'}]
    assert claims and all(c['verdict'] == 'absent' for c in claims)

def test_two_code_quotes_keep_ambiguous_binding_instead_of_skipping_second(tmp_path):
    result = inspect(tmp_path, '`logic.py:1-2` `if value > 10:` 및 `return "ok"`',
                     'if value > 10:\n    return "too large"\nreturn "ok"\n', 'logic.py')
    assert result['status'] == 'INCOMPLETE'
    assert len(atoms(result, 'K1')) == 2
    assert all(c['reason_code'] == 'binding_ambiguous' for c in atoms(result, 'K1'))


def test_separate_reference_descriptions_do_not_form_a_candidate_list(tmp_path):
    result = inspect(tmp_path,
                     '`notes.md:1` (general description)<br>• `notes.md:7` (“alpha line one” 설명)', NOTES)
    claims = atoms(result, 'K1')
    assert any('notes.md:7' in c['text'] and c['verdict'] == 'mismatch' for c in claims)
    assert result['status'] == 'FAIL'


def test_mixed_tail_and_repeated_path_share_one_candidate_list(tmp_path):
    result = inspect(tmp_path, '`notes.md:1, 3`, `notes.md:7` (“alpha line one” 설명)', NOTES)
    quote, = atoms(result, 'K5')
    assert quote['verdict'] == 'match'
    assert quote['source']['candidate_ranges'] == [['notes.md', 1, 1], ['notes.md', 3, 3], ['notes.md', 7, 7]]
