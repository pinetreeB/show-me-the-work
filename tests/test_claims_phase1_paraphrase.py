from __future__ import annotations

import pytest

from tests.test_claims_fix8a import atoms, inspect

SOURCE = ("from typing import Final\n"
          "class Job:\n"
          "    enabled: bool = False\n"
          "    label: str = ''\n"
          "\n"
          "LIMIT: Final = 30\n"
          "\n"
          "def make():\n"
          "    return Job(label=\"x\", enabled=True)\n")


@pytest.mark.parametrize('report,rule', [
    ('`sample.py:9` `Job(..., enabled=True)`', 'R-ell'),
    ('`sample.py:9` `Job(label="x", ..., enabled=True)`', 'R-ell'),
    ('`sample.py:9` `return Job(label="x", ...)`', 'R-ell'),
    ('`sample.py:3` `Job.enabled: bool = False`', 'R-qual'),
    ('`sample.py:6` “LIMIT = 30”', 'R-ann'),
    ('`sample.py:6` `LIMIT = 30`', 'R-ann'),
])
def test_supported_code_rewrite_is_unknown_never_match(tmp_path, report, rule):
    result = inspect(tmp_path, report, SOURCE, 'sample.py')
    relaxed = [c for c in result['claims'] if c['reason_code'] == 'paraphrased_quote']
    assert relaxed and all(c['verdict'] == 'unverifiable' for c in relaxed)
    assert any(rule in c['reason'] for c in relaxed)
    assert result['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('report', [
    '`sample.py:9` `Job(..., enabled=False)`',
    '`sample.py:9` `Job(..., enabled_flag=True)`',
    '`sample.py:9` `Job(enabled=True, ..., label="x")`',
    '`sample.py:3` `Job(..., enabled=True)`',
    '`sample.py:9` `Job(...)`',
    '`sample.py:3` `Job.enabled: int = False`',
    '`sample.py:3` `Job.enabled: bool = True`',
    '`sample.py:3` `Other.enabled: bool = False`',
    '`sample.py:6` `LIMIT = 99`',
    '`sample.py:6` `LIMT = 30`',
    '`sample.py:3` “LIMIT = 30”',
])
def test_value_name_order_owner_or_range_tampering_still_fails(tmp_path, report):
    result = inspect(tmp_path, report, SOURCE, 'sample.py')
    assert result['status'] == 'FAIL'


def test_exact_annotated_quote_still_matches(tmp_path):
    result = inspect(tmp_path, '`sample.py:6` `LIMIT: Final = 30`', SOURCE, 'sample.py')
    assert result['status'] == 'PASS'
    assert not any(c['reason_code'] == 'paraphrased_quote' for c in result['claims'])


@pytest.mark.parametrize('source', [
    'foo(a, True)\nbar(b, False)\n',
    'foo(bar(x, False))\n',
])
def test_ellipsis_cannot_join_other_or_nested_call_arguments(tmp_path, source):
    result = inspect(tmp_path, '`sample.py:1-2` `foo(..., False)`', source, 'sample.py')
    assert result['status'] == 'FAIL'
    assert any(c['reason_code'] == 'quote_mismatch' for c in atoms(result, 'K1'))


def test_short_ellipsis_fragments_do_not_explain_a_quote(tmp_path):
    result = inspect(tmp_path, '`sample.py:1` `f(..., x)`', 'f(a, x)\n', 'sample.py')
    assert result['status'] == 'FAIL'


def test_attribute_belongs_to_the_claimed_class_not_another_class(tmp_path):
    source = 'class Other:\n    unrelated: bool = False\nclass Job:\n    enabled: bool = False\n'
    result = inspect(tmp_path, '`sample.py:1-4` `Other.enabled: bool = False`', source, 'sample.py')
    assert result['status'] == 'FAIL'


def test_qualifier_cannot_match_an_attribute_name_inside_another_assignment_value(tmp_path):
    source = 'class Other:\n    text = "enabled: bool = False"\n'
    result = inspect(tmp_path, '`sample.py:2` `Other.enabled: bool = False`', source, 'sample.py')
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('quote', ['“timeout = 30”', '`timeout = 30`'])
def test_local_annotation_is_not_a_module_constant(tmp_path, quote):
    result = inspect(tmp_path, '`sample.py:2` ' + quote, 'def f():\n    timeout: int = 30\n', 'sample.py')
    assert result['status'] == 'FAIL'
    assert not any(c['reason_code'] == 'paraphrased_quote' for c in result['claims'])


def test_annotation_omission_does_not_hide_an_inserted_comment(tmp_path):
    result = inspect(tmp_path, '`sample.py:6` “LIMIT = 30 # invented comment”', SOURCE, 'sample.py')
    assert result['status'] == 'FAIL'


def test_code_literal_ellipsis_is_not_automatically_a_paraphrase(tmp_path):
    result = inspect(tmp_path, '`sample.py:1` `Job(..., enabled=True)`',
                     'Job(..., enabled=True)\n', 'sample.py')
    assert result['status'] == 'PASS'

def test_plain_quoted_generic_call_ellipsis_is_not_evidence_of_a_match(tmp_path):
    result = inspect(tmp_path, '`sample.py:1` “long_function(...)”', 'long_function(real_value)\n', 'sample.py')
    assert result['status'] == 'FAIL'


def test_numbered_annotation_quote_is_unknown_not_mismatch(tmp_path):
    result = inspect(tmp_path, '`sample.py:6` `6: LIMIT = 30`', SOURCE, 'sample.py')
    assert result['status'] == 'INCOMPLETE'
    assert any(c['reason_code'] == 'paraphrased_quote' for c in result['claims'])

def test_annotation_value_rewrite_respects_its_explicit_range(tmp_path):
    result = inspect(tmp_path, '`sample.py:3` `LIMIT = 30`', SOURCE, 'sample.py')
    assert result['status'] == 'FAIL'
    assert any(c['check_type'] == 'K6' and c['verdict'] == 'mismatch' for c in result['claims'])
