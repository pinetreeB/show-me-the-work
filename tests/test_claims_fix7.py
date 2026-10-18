from __future__ import annotations

import subprocess

import pytest

from smtw.claims.check import run
from tests.claims_support import agy_call, make_case, view


def inspect(tmp_path, report, source='alpha\nspare\nbeta\n', name='notes.md', **kwargs):
    path = 'C:/fixture/' + name
    r, t = make_case(tmp_path, report, [agy_call('view_file', AbsolutePath=path), view(path, source)])
    return run(r, agy=str(t), **kwargs)


def atoms(result, kind):
    return [c for c in result['claims'] if c['check_type'] == kind]


@pytest.mark.parametrize('report,expected', [
    ('`notes.md:1, 3`에 각각 "alpha"·"beta"', ['match', 'match']),
    ('`notes.md:1, 3` "alpha"', ['range_only', 'range_only']),
    ('`notes.md:1, 3` "alpha"·"beta"', ['range_only', 'range_only']),
    ('`notes.md:1, 3`에 각각 "beta"·"alpha"', ['mismatch', 'mismatch']),
    ('`notes.md:1` · `notes.md:3`에 각각 "alpha"·"beta"', ['match', 'match']),
    ('`notes.md:1` · `notes.md:3` "alpha"', ['range_only', 'range_only']),
    ('L1 · `notes.md:3`에 각각 "alpha"·"beta"', ['match', 'match']),
    ('`notes.md:1, 3` "alpha"·"beta"; 다음 작업은 각각 처리한다.', ['range_only', 'range_only']),
    ('`notes.md:1, 3` "alpha"·"beta", 다음 작업은 각각 처리한다.', ['range_only', 'range_only']),
    ('`notes.md:1, 3` "alpha"·"beta", respectively.', ['match', 'match']),
])
def test_multiple_references_keep_each_claim(tmp_path, report, expected):
    result = inspect(tmp_path, report)
    claims = atoms(result, 'K1')
    assert [c['verdict'] for c in claims] == expected
    if 'unverifiable' in expected:
        assert all(c['reason_code'] == 'binding_ambiguous' for c in claims)
        assert result['status'] == 'INCOMPLETE'
    if 'range_only' in expected:
        assert result['status'] == 'INCOMPLETE'
    if 'mismatch' in expected:
        assert result['status'] == 'FAIL'


@pytest.mark.parametrize('quotes,marker,expected', [
    ('"alpha"·"beta"', '각각', 'match'),
    ('"beta"·"alpha"', '각각', 'mismatch'),
    ('"alpha"·"beta"', '', 'range_only'),
])
def test_multiple_explicit_files_share_only_certain_ordered_bindings(tmp_path, quotes, marker, expected):
    report, transcript = make_case(tmp_path, f'`alpha.md:1` · `beta.md:1`에 {marker} {quotes}', [
        agy_call('view_file', AbsolutePath='C:/fixture/alpha.md'), view('C:/fixture/alpha.md', 'alpha\n'),
        agy_call('view_file', AbsolutePath='C:/fixture/beta.md'), view('C:/fixture/beta.md', 'beta\n'),
    ])
    result = run(report, agy=str(transcript))
    claims = atoms(result, 'K1')
    assert len(claims) == 2
    assert [c['verdict'] for c in claims] == [expected, expected]
    assert (result['status'] == 'FAIL') == (expected == 'mismatch')
    if expected == 'unverifiable':
        assert all(c['reason_code'] == 'binding_ambiguous' for c in claims)


@pytest.mark.parametrize('report', [
    '`notes.md` 이전 보고는 총 53줄이라고 주장했지만 실제 총 3줄이다.',
    'They reported that `notes.md` has 53 lines, but actually it has 3 lines.',
    'The old report claimed `notes.md` has 53 lines, but actually it has 3 lines.',
])
def test_reported_metrics_leave_actual_claim_checkable(tmp_path, report):
    result = inspect(tmp_path, report, 'alpha\nbeta\ngamma')
    claims = atoms(result, 'K2')
    assert any(c['reason_code'] == 'reported_claim' for c in claims)
    assert any(c['verdict'] == 'match' and '3' in c['text'] for c in claims)
    assert result['status'] == 'INCOMPLETE'


def test_reported_value_and_wrong_actual_value(tmp_path):
    result = inspect(tmp_path, '`settings.json` 이전 보고가 `size: 1234`라고 적었지만 실제 `size: 99`이다.',
                     '{"size": 4}', 'settings.json')
    claims = atoms(result, 'K6')
    assert claims[0]['reason_code'] == 'reported_claim'
    assert claims[1]['verdict'] == 'mismatch'
    assert result['status'] == 'FAIL'


def test_reported_space_separated_value_is_retained(tmp_path):
    result = inspect(tmp_path, '`settings.json` X가 `size 1234`라고 적은 것은 틀림, 실제 `size 4`이다.',
                     '{"size": 4}', 'settings.json')
    claims = atoms(result, 'K6')
    assert len(claims) == 2
    assert claims[0]['reason_code'] == 'reported_claim'
    assert claims[1]['verdict'] == 'match'


@pytest.mark.parametrize('description', ['검증 도구 `python 3`', '실제 검증 도구 `git 2`'])
def test_tool_versions_do_not_become_new_entity_value_claims(tmp_path, description):
    result = inspect(tmp_path, '`notes.md` ' + description)
    assert not atoms(result, 'K4')
    assert not atoms(result, 'K6')
    assert result['status'] == 'INCOMPLETE'


def test_reported_count_does_not_suppress_wrong_correction(tmp_path):
    result = inspect(tmp_path, 'The old report reported `notes.md` as 53 lines but actually 99 lines.')
    claims = atoms(result, 'K2')
    assert claims[0]['reason_code'] == 'reported_claim'
    assert claims[1]['verdict'] == 'mismatch'
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('report,kind', [
    ('이전 보고가 claimed: `notes.py:1` `wrong = condition()`', 'K1'),
    ('`notes.py` 이전 보고가 "wrong = condition()"이라고 claimed했다', 'K5'),
])
def test_reported_context_never_hides_a_tampered_literal_quote(tmp_path, report, kind):
    result = inspect(tmp_path, report, 'right = condition()\n', 'notes.py')
    assert any(c['verdict'] == 'mismatch' for c in atoms(result, kind))
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('claim,kind', [('L1-L248', 'K1'), ('총 120줄짜리', 'K2'), ('크기 964 bytes', 'K2')])
def test_single_file_heading_keeps_checkable_binding(tmp_path, claim, kind):
    result = inspect(tmp_path, '### `notes.md`\n' + claim)
    assert atoms(result, kind)[0]['reason_code'] in {'range_exceeded', 'value_mismatch'}
    assert atoms(result, kind)[0]['verdict'] == 'mismatch'
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('report,kind', [('`notes.md:248`', 'K1'), ('`notes.md`는 120줄', 'K2'),
                                         ('`notes.md` 크기 964 bytes', 'K2')])
def test_explicit_wrong_metrics_still_fail(tmp_path, report, kind):
    result = inspect(tmp_path, report)
    assert atoms(result, kind)[0]['verdict'] == 'mismatch'
    assert result['status'] == 'FAIL'


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True).stdout.decode().strip()


def test_repository_search_uses_fixed_commit_and_preserves_fabrications(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    git(repo, 'config', 'user.name', 'Fixture')
    (repo / 'other.py').write_text('def real_alpha(): pass\ndef real_beta(): pass\ndef real_gamma(): pass\n', encoding='utf-8')
    git(repo, 'add', 'other.py')
    git(repo, 'commit', '-qm', 'fixture')
    revision = git(repo, 'rev-parse', 'HEAD')
    (repo / 'other.py').write_text('def invented_alpha(): pass\n', encoding='utf-8')
    git(repo, 'commit', '-qam', 'different current state')
    report = '`notes.md` 함수 ' + ' '.join('`'+s+'`' for s in (
        'real_alpha', 'real_beta', 'real_gamma', 'invented_alpha', 'invented_beta', 'invented_gamma'))
    result = inspect(tmp_path, report, repos=[str(repo)], commits={str(repo): revision})
    claims = atoms(result, 'K4')
    assert len(claims) == 6
    assert [c['reason_code'] for c in claims[:3]] == ['found_outside_scope'] * 3
    assert [c['verdict'] for c in claims[3:]] == ['absent'] * 3
    assert result['status'] == 'FAIL'


def test_repository_search_failure_cannot_confirm_absence(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    result = inspect(tmp_path, '`notes.md` 함수 `missing_helper`', repos=[str(repo)],
                     commits={str(repo): 'f' * 40})
    claim = atoms(result, 'K4')[0]
    assert claim['verdict'] == 'unverifiable'
    assert claim['reason_code'] == 'repository_search_incomplete'


def test_other_file_value_never_hides_explicit_wrong_value(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    (repo / 'other.json').write_text('{"size": 99}', encoding='utf-8')
    git(repo, 'add', 'other.json')
    git(repo, '-c', 'user.email=fixture@example.invalid', '-c', 'user.name=Fixture', 'commit', '-qm', 'fixture')
    result = inspect(tmp_path, '`settings.json` 설정 키 `size: 99`', '{"size": 4}', 'settings.json',
                     repos=[str(repo)], commits={str(repo): git(repo, 'rev-parse', 'HEAD')})
    assert atoms(result, 'K6')[0]['verdict'] == 'mismatch'
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('wording', [
    '새로 `EvidenceIngestorX` 클래스를 추가한다', '`NEW_FLAG`를 도입할 것을 제안한다',
    '제안: `NEW_FLAG`', '신설: `NEW_FLAG`', 'we will add `new_helper()`',
    '`new_helper`를 추가하면 해결된다', '`new_helper` 함수를 추가해야 한다',
    '함수 `new_helper`가 필요하다', 'Consider adding `new_helper`',
    'would use `future_helper` if enabled', '상수 `DIRECT_QUOTE`를 새로 제안한다',
    '`future_helper`를 가정하면 된다', 'should add function `new_helper`',
])
def test_proposals_are_retained_as_unverifiable(tmp_path, wording):
    result = inspect(tmp_path, '`notes.md` ' + wording)
    claims = atoms(result, 'K4')
    assert claims
    assert all(c['verdict'] == 'unverifiable' and c['reason_code'] == 'proposed_name' for c in claims)
    assert result['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('wording', ['추가된 함수 `ghost_helper`가 있다', '`ghost_helper` 함수를 추가했다',
    '`ghost_helper`가 정의되어 있다', 'function `ghost_helper` is used if enabled',
    '함수 `ghost_helper` 추가 (신규)', 'should add `new_helper`, but `ghost_helper` is defined'])
def test_factual_fabrications_are_not_proposals(tmp_path, wording):
    result = inspect(tmp_path, '`notes.md` ' + wording)
    assert any(c['verdict'] == 'absent' and 'ghost_helper' in c['text'] for c in atoms(result, 'K4'))
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('report', ['R1 `git`', 'R4-N1 `raw`'])
def test_short_item_token_does_not_create_a_k9_atom(tmp_path, report):
    result = inspect(tmp_path, '`notes.md` ' + report, '# R1\nnever log secrets\n')
    assert not atoms(result, 'K9')
    assert result['status'] == 'INCOMPLETE'


def test_item_date_is_retained_as_unverifiable(tmp_path):
    result = inspect(tmp_path, '`notes.md` #129 20261018', '# R1\nnever log secrets\n')
    claims = atoms(result, 'K9')
    assert claims and all(c['reason_code'] == 'item_token_only' for c in claims)


@pytest.mark.parametrize('description', ['R1: "always log secrets"', 'R1 always log secrets'])
def test_item_description_still_fails(tmp_path, description):
    result = inspect(tmp_path, '`notes.md` ' + description, '# R1\nnever log secrets\n')
    assert atoms(result, 'K9')[0]['verdict'] == 'mismatch'
    assert result['status'] == 'FAIL'


def test_missing_setting_key_and_value_still_absent(tmp_path):
    result = inspect(tmp_path, '`settings.json` 설정 키 `missingKey: 2`', '{"realKey": 2}', 'settings.json')
    assert atoms(result, 'K4')[0]['verdict'] == 'absent'
    assert atoms(result, 'K6')[0]['verdict'] == 'absent'
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('report', ['`notes.md:1` "never publish secrets"', '`notes.md` "never publish secrets"',
    '`notes.md:1` `if ready and permitted: send()`'])
def test_quote_tampering_still_fails(tmp_path, report):
    source = 'if ready: send()\n' if 'if ready' in report else 'never log secrets\n'
    result = inspect(tmp_path, report, source)
    assert any(c['verdict'] == 'mismatch' for c in result['claims'] if c['check_type'] in {'K1', 'K5'})
    assert result['status'] == 'FAIL'
