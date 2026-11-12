"""Fix2 extraction precision contracts using only synthetic report text."""
from __future__ import annotations

import pytest

from smtw.claims.check import run
from smtw.claims.extract import extract
from smtw.claims.model import Budget
from tests.claims_support import agy_call, make_case, view


def claims(text):
    return extract(text, Budget(10))[0]


def kinds(text):
    return [claim.check_type for claim in claims(text)]


@pytest.mark.parametrize('report', [
    '`engine.py` 크기 , B',
    '`engine.py` 총 , 줄',
    ',건 중 2건',
    '3건 중 ,건',
])
def test_fix2_comma_only_metrics_are_preserved_as_unverifiable_atoms(report):
    found = claims(report)
    assert len(found) == 1
    assert found[0].check_type in {'K2', 'K7'}
    assert found[0].malformed_number is True


@pytest.mark.parametrize(('report', 'negated'), [
    ('`engine.py`에는 `argparse` 의존이 없다.', None),
    ('`engine.py`에 `new_helper` 함수를 추가할 것을 권고한다.', True),
    ('`engine.py`에서 `future_helper`를 가정하면 된다.', False),
    ('`engine.py`에는 `optional_helper`를 권장하지 않는다.', True),
    ('`engine.py` would use `future_helper` if enabled.', False),
])
def test_fix2_negated_recommended_and_hypothetical_identifiers_are_not_k4(report, negated):
    found = [claim for claim in claims(report) if claim.check_type == 'K4']
    if negated is None:
        assert found == []
        return
    assert len(found) == 1
    assert found[0].negated_context is negated


@pytest.mark.parametrize('token', ['synthrepo', 'pytest', 'PASS', 'view_file', 'owner', 'enabled', 'file'])
def test_fix2_inherited_path_does_not_promote_general_tool_or_repository_name(token):
    assert 'K4' not in kinds('### `engine.py` 분석\n`'+token+'`')


@pytest.mark.parametrize('token', ['owner', 'enabled', 'file'])
def test_fix2_direct_path_does_not_promote_general_words(token):
    assert 'K4' not in kinds('`engine.py` 분석 `'+token+'`')


def test_fix2_explicit_code_context_allows_plain_variable_names():
    assert [c.value for c in claims('`engine.py` 변수 `enabled`') if c.check_type == 'K4'] == ['enabled']


@pytest.mark.parametrize('path', ['config.py', 'field.py', 'function.py'])
@pytest.mark.parametrize('quoted', [False, True])
def test_fix2_file_name_cannot_supply_identifier_context(path, quoted):
    display = '`'+path+'`' if quoted else path
    assert 'K4' not in kinds(display + ' 설명 `owner`')


def test_fix2_backtick_word_cannot_supply_its_own_code_context():
    assert 'K4' not in kinds('`engine.py` 설명 `config`')


def test_fix2_explicit_code_context_is_separate_from_path_and_token():
    assert [c.value for c in claims('`config.py` 변수 `owner`') if c.check_type == 'K4'] == ['owner']


def test_fix2_inherited_code_shaped_identifier_stays_k4():
    assert [claim.value for claim in claims('### `engine.py` 분석\n`parse_args`') if claim.check_type == 'K4'] == ['parse_args']


def test_fix2_sha_prefix_does_not_create_byte_metric():
    report = '`engine.py` 커밋 `68b0683d1234567890abcdef1234567890abcdef`'
    assert 'K6' not in kinds(report)
    assert 'K3' in kinds(report)


def test_fix2_reference_binds_to_fence_after_one_blank_line(tmp_path):
    path = 'C:/sample/engine.py'
    report, transcript = make_case(tmp_path, '`engine.py:1`\n\n```py\nwrong source code\n```', [
        agy_call('view_file', AbsolutePath=path), view(path, 'right source code')])
    result = run(report, agy=str(transcript))
    claim = next(item for item in result['claims'] if item['check_type'] == 'K1')
    assert (claim['verdict'], claim['reason_code']) == ('mismatch', 'quote_mismatch')


def test_fix2_reference_does_not_cross_prose_before_fence(tmp_path):
    path = 'C:/sample/engine.py'
    report, transcript = make_case(tmp_path, '`engine.py:1`\n설명 문장입니다.\n```py\nwrong source code\n```', [
        agy_call('view_file', AbsolutePath=path), view(path, 'right source code')])
    result = run(report, agy=str(transcript))
    claim = next(item for item in result['claims'] if item['check_type'] == 'K1')
    assert claim['verdict'] == 'range_only'
