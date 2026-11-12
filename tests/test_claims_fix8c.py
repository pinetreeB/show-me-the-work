from __future__ import annotations

import pytest

from smtw.claims.check import run
from tests.claims_support import agy_call, make_case, view


def inspect_versions(tmp_path, report, versions, *, partial=False):
    path = 'C:/synthetic/project/logic.py'
    rows = []
    for index, source in enumerate(versions):
        rows.extend([agy_call('view_file', AbsolutePath=path),
                     view(path, source, end=1 if partial and index == 0 else None)])
    report_path, transcript = make_case(tmp_path, report, rows)
    return run(report_path, agy=str(transcript))


def quotes(result):
    return [c for c in result['claims'] if c['check_type'] in {'K1', 'K5'}
            and c['reason_code'] != 'range_only']


@pytest.mark.parametrize('report', [
    '`logic.py` "return check_user() and invented_rule()"',
    '`logic.py:1` `return invented_rule()`',
])
def test_quote_missing_from_every_observed_version_is_mismatch(tmp_path, report):
    result = inspect_versions(tmp_path, report,
                              ['return check_user()\n', 'return check_admin()\n'])
    targets = quotes(result)
    assert targets and result['status'] == 'FAIL'
    assert all(c['verdict'] == 'mismatch' and c['reason_code'] == 'quote_mismatch' for c in targets)
    for claim in targets:
        assert claim['source']['quote_check']['version_count'] == 2
        assert len(claim['source']['quote_check']['observations']) == 2
        assert all(e['location'] for e in claim['source']['quote_check']['observations'])


@pytest.mark.parametrize('quote', ['return check_user()', 'return check_admin()'])
@pytest.mark.parametrize('reference', ['`logic.py`', '`logic.py:1`'])
def test_quote_in_either_observed_version_is_not_a_false_mismatch(tmp_path, quote, reference):
    result = inspect_versions(tmp_path, reference + ' "' + quote + '"',
                              ['return check_user()\n', 'return check_admin()\n'])
    assert quotes(result)
    assert all(c['verdict'] == 'unverifiable' for c in quotes(result))
    assert result['status'] != 'FAIL'


def test_shifted_range_searches_whole_versions_and_discloses_fallback(tmp_path):
    result = inspect_versions(tmp_path, '`logic.py:1` `return kept_value()`',
                              ['return unrelated()\nreturn kept_value()\n',
                               'return other_value()\nreturn kept_value()\n'])
    claim = quotes(result)[0]
    assert claim['verdict'] == 'unverifiable'
    assert claim['source']['quote_check']['whole_version_fallback'] is True
    assert result['status'] != 'FAIL'


def test_ambiguous_range_checks_all_versions_and_all_candidates(tmp_path):
    result = inspect_versions(tmp_path, '`logic.py:1, 2` `return invented_rule()`',
                              ['return first_value()\nreturn second_value()\n',
                               'return third_value()\nreturn fourth_value()\n'])
    assert quotes(result)
    for claim in quotes(result):
        assert (claim['verdict'], claim['reason_code']) == ('mismatch', 'quote_mismatch')
        proof = claim['source']['quote_check']
        assert proof['version_count'] == 2 and proof['candidate_count'] == 3
        assert len(proof['matrix']) == 6


def test_ambiguous_quote_present_in_one_version_stays_unverifiable(tmp_path):
    result = inspect_versions(tmp_path, '`logic.py:1, 2` `return old_value()`',
                              ['return other_value()\nreturn old_value()\n',
                               'return other_value()\nreturn new_value()\n'])
    assert quotes(result)
    assert all(c['verdict'] == 'unverifiable' for c in quotes(result))
    assert result['status'] != 'FAIL'


def test_partial_version_cannot_prove_quote_absent_everywhere(tmp_path):
    result = inspect_versions(tmp_path, '`logic.py:1` `return invented_rule()`',
                              ['return first_value()\nreturn unseen_value()\n',
                               'return second_value()\nreturn another_value()\n'], partial=True)
    claim = quotes(result)[0]
    assert claim['verdict'] == 'unverifiable'
    assert result['status'] != 'FAIL'


def test_ambiguous_range_outside_one_version_keeps_accurate_other_version(tmp_path):
    result = inspect_versions(tmp_path, '`logic.py:1, 2` `return old_value()`',
                              ['return first_value()\nreturn old_value()',
                               'return new_value()'])
    assert quotes(result)
    assert all(c['verdict'] == 'unverifiable' for c in quotes(result))
    assert result['status'] != 'FAIL'


def test_same_relevant_quote_in_both_versions_still_matches(tmp_path):
    result = inspect_versions(tmp_path, '`logic.py:1` `return kept_value()`',
                              ['return kept_value()\nreturn old_value()',
                               'return kept_value()\nreturn new_value()'])
    assert quotes(result)
    assert all(c['verdict'] == 'match' for c in quotes(result))


def test_invalid_numbered_quote_is_not_relaxed_by_whole_version_fallback(tmp_path):
    result = inspect_versions(tmp_path, '`logic.py:1` `2: return kept_value()`',
                              ['return first_value()\nreturn kept_value()',
                               'return second_value()\nreturn kept_value()'])
    assert quotes(result)
    assert all(c['verdict'] != 'match' for c in quotes(result))


@pytest.mark.parametrize('condition,verdict', [('10', 'unverifiable'), ('99', 'mismatch')])
def test_adjacent_candidate_join_is_checked_in_every_version(tmp_path, condition, verdict):
    result = inspect_versions(tmp_path,
                              f'`logic.py:1, 2` `if value > {condition}: return "too large"`',
                              ['if value > 10:\n    return "too large"\n',
                               'if value > 20:\n    return "too large"\n'])
    claim, = quotes(result)
    assert claim['verdict'] == verdict
    assert claim['source']['joined_candidate_ranges'] == [['logic.py', 1, 2]]
    assert claim['source']['quote_check']['version_count'] == 2


def test_long_line_candidate_alternative_survives_version_fallback(tmp_path):
    result = inspect_versions(tmp_path,
                              "`logic.py:1` 실측: `value = 'invented' ~ flags`, `value = 'kept' ~ flags`",
                              ["padding " * 70 + "value = 'kept' ~ flags\n",
                               "different " * 70 + "value = 'kept' ~ flags\n"])
    assert quotes(result)
    assert all(c['verdict'] != 'mismatch' for c in quotes(result))


def test_failed_read_cannot_prove_absence_from_every_version(tmp_path):
    from tests.claims_support import generic
    path = 'C:/synthetic/project/logic.py'
    report, transcript = make_case(tmp_path, '`logic.py:1` `return invented_rule()`', [
        agy_call('view_file', AbsolutePath=path), view(path, 'return actual_rule()\n'),
        agy_call('view_file', AbsolutePath=path), generic('Error: failed to read file', status='ERROR'),
    ])
    result = run(report, agy=str(transcript))
    assert quotes(result)
    assert all(c['verdict'] == 'unverifiable' for c in quotes(result))
