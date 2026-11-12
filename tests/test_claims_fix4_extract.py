"""Fix4 negated-context and malformed-number boundaries with synthetic reports."""
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
    '`module.py`에는 `legacy_symbol` 의존이 없다.',
    '`module.py`에는 `legacy_symbol` 의존이 없습니다.',
    '`module.py`는 `legacy_symbol`를 사용하지 않습니다.',
    '`module.py`는 `legacy_symbol`를 쓰지 않는다.',
    '`module.py`에 `new_symbol` 함수를 추가할 것을 권장합니다.',
    '`module.py`에 `new_symbol` 함수를 추가할 것을 권고한다.',
    '`module.py`에서 `open_session()` 호출이 제거되었습니다.',
    '`module.py`에서 `open_session()` 호출이 제거되었다.',
    '`module.py`에서 `open_session()`은 제거했다.',
    'The file `module.py` has no `legacy_symbol` dependency.',
    '`module.py` does not use `legacy_symbol` anymore.',
    'There is no `legacy_symbol` in `module.py`.',
    '`module.py`에 `legacy_symbol`가 존재하지 않는다.',
    '`module.py`에는 `legacy_symbol`가 포함되어 있지 않다.',
    '`module.py`에 `legacy_symbol` import가 없다.',
    '`module.py`에서 `legacy_symbol` 함수는 쓰이지 않습니다.',
    '`module.py`: `legacy_symbol` 의존 없음.',
    '`module.py`에 `legacy_symbol` 의존이 없어요.',
    '`module.py`: `legacy_symbol` 호출 제거됨.',
    '`module.py`에서 `legacy_symbol` 호출을 제거했습니다.',
    '`module.py`에서 `legacy_symbol` 사용을 권고합니다.',
    '`module.py` no longer calls `legacy_symbol`.',
    'The call to `legacy_symbol` was removed from `module.py`.',
    '`module.py` 함수 `legacy_symbol`는 의존은 없다.',
])
def test_fix4_negated_suffixes_preserve_the_identifier_as_unverifiable(report):
    found = k4(report)
    assert len(found) == 1
    assert found[0].negated_context is True


def test_fix4_bare_anhneunda_remains_a_negated_identifier_context():
    found = k4('`module.py` 함수 `legacy_symbol`는 않는다.')
    assert len(found) == 1
    assert found[0].negated_context is True


@pytest.mark.parametrize('particle', ['은', '는', '이', '가', '을', '를'])
def test_fix4_descriptor_particles_keep_negative_context_local_to_the_identifier(particle):
    found = k4(f'`module.py` 함수 `legacy_symbol` 의존{particle} 없다.')
    assert len(found) == 1
    assert found[0].negated_context is True


@pytest.mark.parametrize('report', [
    '`module.py` 함수 `current_symbol`가 정의돼 있다.',
    'In `module.py`, function `current_symbol` is defined.',
    '`module.py` 함수 `current_symbol`가 있어 오류가 발생하지 않는다.',
    'function `current_symbol` in `module.py` is used if the flag is set.',
    '`module.py` 함수 `current_symbol`가 있고 다른 모듈에도 쓰인다.',
    '`module.py`에 추가된 함수 `current_symbol`가 있다.',
    '`module.py` 함수 `current_symbol`의 값은 `not configured`이다.',
    '`module.py` 함수 `current_symbol`는 존재한다.',
    'In `module.py`, function `current_symbol` is available.',
    '`module.py` 함수 `current_symbol`는 유지된다.',
    'In `module.py`, function `current_symbol` uses a fallback.',
    '`module.py` 함수 `current_symbol`가 정상적으로 동작한다.',
    '`module.py` 함수 `current_symbol`를 호출했다.',
])
def test_fix4_factual_identifier_claims_remain_absent_when_the_complete_source_lacks_them(tmp_path, report):
    source = tmp_path / 'module.py'
    source.write_text('def another_symbol():\n    pass\n', encoding='utf-8')
    found = k4(report)
    assert [claim.value for claim in found] == ['current_symbol']
    assert found[0].negated_context is False
    report_path, _ = make_case(tmp_path, report)
    result = run(report_path, sources={'module.py': str(source)})
    claim = next(item for item in result['claims'] if item['check_type'] == 'K4')
    assert (claim['verdict'], claim['reason_code']) == ('absent', 'not_found')


@pytest.mark.parametrize('report, kind', [
    ('`module.py` 크기 , B\n`module.py` 총 3 줄', 'K2'),
    (',건 중 2건\n3건 중 2건', 'K7'),
])
def test_fix4_malformed_number_atom_does_not_stop_later_claims(tmp_path, report, kind):
    report_path, _ = make_case(tmp_path, report)
    result = run(report_path)
    found = [claim for claim in result['claims'] if claim['check_type'] == kind]
    assert found[0]['reason_code'] == 'malformed_number'
    assert len(found) == 2


def test_fix4_malformed_metric_keeps_same_line_number_and_identifier_claims():
    found = claims('`module.py` 크기 , B; 3건 중 2건; 함수 `current_symbol`가 정의돼 있다.')
    metrics = [claim for claim in found if claim.check_type == 'K2']
    arithmetic = [claim for claim in found if claim.check_type == 'K7']
    identifiers = [claim for claim in found if claim.check_type == 'K4']
    assert [(claim.value, claim.malformed_number) for claim in metrics] == [(',', True)]
    assert [(claim.value, claim.entity) for claim in arithmetic] == [('3', '2')]
    assert [claim.value for claim in identifiers] == ['current_symbol']
