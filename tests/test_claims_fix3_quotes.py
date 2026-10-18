"""Synthetic fix3 boundaries for ellipsis comparison without weak wildcards."""
from __future__ import annotations

import pytest

from smtw.claims.check import check_one, run
from smtw.claims.model import Budget, Claim, Evidence
from tests.claims_support import agy_call, make_case, view


@pytest.mark.parametrize('quote', ['def … ok', 'import ... return'])
@pytest.mark.parametrize('kind', ['K1', 'K5'])
def test_weak_fragments_use_literal_comparison(kind, quote):
    text = 'import data\ndef example():\n    return ok'
    evidence = Evidence(text=text, lines=dict(enumerate(text.splitlines(), 1)), total_lines=3, complete=True)
    claim = Claim(1, quote, kind, quote=quote, start=1 if kind == 'K1' else 0, end=3)
    result = check_one(claim, evidence, Budget(5))
    assert (result.verdict, result.code) == ('mismatch', 'quote_mismatch')


@pytest.mark.parametrize('length,verdict', [(7, 'mismatch'), (8, 'match'), (9, 'match')])
@pytest.mark.parametrize('gap', ['…', '...'])
def test_longest_fragment_boundary(length, verdict, gap):
    fragment = 'a' * length
    claim = Claim(1, '', 'K5', quote=f'{fragment} {gap} end')
    result = check_one(claim, Evidence(text=fragment+' omitted end', complete=True), Budget(5))
    assert result.verdict == verdict


def test_short_fragments_can_still_match_literal_ellipsis():
    quote = 'import … return'
    result = check_one(Claim(1, quote, 'K5', quote=quote), Evidence(text=quote, complete=True), Budget(5))
    assert result.verdict == 'match'


def test_long_fragments_still_require_order():
    claim = Claim(1, '', 'K5', quote='first fragment … last fragment')
    result = check_one(claim, Evidence(text='last fragment then first fragment', complete=True), Budget(5))
    assert result.verdict == 'mismatch'


def test_weak_fragments_in_incomplete_source_remain_unverified():
    result = check_one(Claim(1, '', 'K5', quote='import … return'),
                       Evidence(text='import data\nreturn ok', complete=False), Budget(5))
    assert result.verdict == 'unverifiable'


@pytest.mark.parametrize('quote', ['def … ok', 'import ... return'])
def test_weak_fragments_cannot_make_report_pass(tmp_path, quote):
    path = 'C:/sample/code.py'
    report, transcript = make_case(tmp_path, '`code.py` “'+quote+'”', [
        agy_call('view_file', AbsolutePath=path), view(path, 'import data\ndef example():\n    return ok')])
    result = run(report, agy=str(transcript))
    assert result['status'] == 'FAIL'
    assert any(c['check_type'] == 'K5' and c['verdict'] == 'mismatch' for c in result['claims'])
