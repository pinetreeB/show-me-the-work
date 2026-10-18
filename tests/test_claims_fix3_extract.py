"""Fix3 extraction boundaries using only synthetic report text."""
from __future__ import annotations

import pytest

from smtw.claims.check import run
from smtw.claims.extract import extract
from smtw.claims.model import Budget
from tests.claims_support import make_case


def claims(text):
    return extract(text, Budget(10))[0]


def k4(text):
    return [claim for claim in claims(text) if claim.check_type == 'K4']


@pytest.mark.parametrize('report', [
    '`engine.py`에 함수 `ghost_helper`가 정의돼 있고 오류는 없다.',
    'In `engine.py`, function `ghost_helper` is defined, and it does not raise.',
    '`engine.py`에 함수 `ghost_helper`가 있어 오류가 발생하지 않는다.',
    'function `ghost_helper` in `engine.py` is used if the flag is set',
    '`engine.py`에 함수 `ghost_helper`가 있고, 다른 모듈에도 쓰인다.',
    '`engine.py`에 추가된 함수 `ghost_helper`가 있다.',
    '`engine.py` 함수 `ghost_helper`의 값은 `not configured`이다.',
])
def test_fix3_unrelated_or_excluded_cues_keep_identifier_claim(report):
    found = k4(report)
    assert [claim.value for claim in found] == ['ghost_helper']
    assert found[0].negated_context is False


@pytest.mark.parametrize('report', [
    '`engine.py`에서 `parse_args`는 쓰지 않는다.',
    '`engine.py`에 `new_helper` 함수를 추가할 것을 권고한다.',
    '`engine.py`에는 `optional_helper`를 권장하지 않는다.',
    'In `engine.py`, function `ghost_helper` is not used.',
    '`engine.py`에서 `open_session()` 호출이 제거되었다.',
    '### `engine.py`\n이 모듈은 `parse_args` 함수를 호출하지 않는다.',
    '`engine.py` `ghost_helper` not used.',
    '`engine.py` has no function `ghost_helper`.',
    '`engine.py` `ghost_helper` should not be used.',
    '`engine.py` `ghost_helper`를 제거해야 한다.',
])
def test_fix3_narrow_negative_or_recommendation_keeps_unverifiable_claim(report):
    found = k4(report)
    assert len(found) == 1
    assert found[0].negated_context is True


def test_fix3_general_dependency_name_remains_excluded():
    assert k4('`engine.py`에는 `argparse` 의존이 없다.') == []


def test_fix3_negative_context_reaches_checker_as_unverifiable(tmp_path):
    report, transcript = make_case(tmp_path, '`engine.py` 함수 `ghost_helper`는 쓰지 않는다.')
    result = run(report, agy=str(transcript))
    claim = next(item for item in result['claims'] if item['check_type'] == 'K4')
    assert (claim['verdict'], claim['reason_code']) == ('unverifiable', 'negated_context')


@pytest.mark.parametrize('report, expected', [
    ('`big.log` 크기 5381751 B', ['K2']),
    ('`big.log` 총 1234567줄', ['K2']),
    ('`big.log` 커밋 `68b0683d1234567890abcdef1234567890abcdef`', ['K3']),
])
def test_fix3_hex_sha_requires_a_to_f(report, expected):
    assert [claim.check_type for claim in claims(report)] == expected


def test_fix3_proposed_fence_is_not_attached_to_line_reference():
    found = claims('`engine.py:8` 의 조건을 아래처럼 바꿔야 한다.\n\n```py\nif value > 100:\n    return "too large"\n```')
    reference = next(claim for claim in found if claim.check_type == 'K1')
    assert reference.quote == ''


@pytest.mark.parametrize('path', ['should.py', 'proposed.py'])
def test_fix3_proposal_word_in_path_does_not_block_fence_attachment(path):
    found = claims(f'`{path}:1`\n\n```py\nsource code\n```')
    reference = next(claim for claim in found if claim.check_type == 'K1')
    assert reference.quote == 'source code'
