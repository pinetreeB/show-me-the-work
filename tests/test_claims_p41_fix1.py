"""Synthetic reproductions of both independent p4-1 reviews."""
import pytest

from smtw.claims.check import run
from tests.claims_support import make_case
from tests.test_claims_p41 import atoms, inspect, repository


CODE_TAILS = [
    ('import alpha as a, beta as b', 'import alpha as a, beta as b, gamma as c'),
    ('x = (a in u, b in v)', 'a in u, b in v, c in w'),
    ('res = await alpha / await beta', 'await alpha / await beta / await gamma'),
    ('void f(int alpha, int beta) {}', 'int alpha, int beta, int gamma'),
    ('import os', 'import os / import sys'),
    ('value is ready', 'value is ready / flag is ready'),
    ('import sys', 'import os 3 items'),
    ('yield total', 'yield total / 2 items'),
    ('global alpha', 'global alpha / global beta'),
    ('nonlocal alpha', 'nonlocal alpha / nonlocal beta'),
    ('del alpha', 'del alpha / del beta'),
    ('raise alpha', 'raise alpha / raise beta'),
    ('assert alpha', 'assert alpha / assert beta'),
    ('with alpha', 'with alpha / with beta'),
    ('async alpha', 'async alpha / async beta'),
    ('float alpha, float beta', 'float alpha, float beta, float gamma'),
    ('bool alpha, bool beta', 'bool alpha, bool beta, bool gamma'),
    ('char alpha, char beta', 'char alpha, char beta, char gamma'),
    ('string alpha, string beta', 'string alpha, string beta, string gamma'),
    ('요청 / 응답', '요청 / 응답 / 오류'),
]


@pytest.mark.parametrize('source,quote', CODE_TAILS)
def test_claims_review_code_tail_cannot_be_a_label(tmp_path, source, quote):
    result = inspect(tmp_path, f'`logic.py:1` `{quote}`', source+'\n')
    claim = atoms(result,'K1')[0]
    assert (claim['verdict'],claim['reason_code']) == ('mismatch','quote_mismatch')
    assert result['status']=='FAIL'


@pytest.mark.parametrize('source', ['if a:  # 3 items\n', 'if a: print("3 items")\n'])
@pytest.mark.parametrize('reference', ['`logic.py:1`', '`logic.py:1-2`', '`logic.py:1`, `logic.py:2`'])
def test_claims_label_in_source_cannot_rescue_later_changed_quote(tmp_path, source, reference):
    result = inspect(tmp_path, f'{reference} `3 items` `if a and b:`', source+'pass\n')
    changed = [c for c in result['claims'] if 'if a and b:' in c['text']]
    assert changed
    assert any(c['verdict']=='mismatch' and c['reason_code']=='quote_mismatch' for c in changed)
    assert result['status']=='FAIL'


def test_claims_label_cannot_hide_a_false_line_range(tmp_path):
    result = inspect(tmp_path, '`logic.py:90` `3 items`', 'if a:\n')
    assert any(c['reason_code']=='range_exceeded' and c['verdict']=='mismatch' for c in result['claims'])
    assert any(c['reason_code']=='summary_label' for c in result['claims'])
    assert result['status']=='FAIL'


NON_DEFINITIONS = [
    'if moved_helper:\n    pass\n',
    'some_call(moved_helper=1)\n',
    'arr[moved_helper:]\n',
    'd = {"please call moved_helper": 1}\n',
    'table = {"moved_helper": 1}\n',
    'while moved_helper:\n    break\n',
    'some_call(\n    moved_helper=1,\n)\n',
    'arr[\n    moved_helper:\n]\n',
    'table = {\n    "moved_helper": 1,\n}\n',
    'text = "def moved_helper(): pass"\n',
    '# def moved_helper(): pass\n',
    'moved_helper: int\n',
    'if moved_helper == candidate: pass\n',
    'def wrapper(moved_helper=1): pass\n',
]


@pytest.mark.parametrize('other', NON_DEFINITIONS)
@pytest.mark.parametrize('source,reason', [('moved_helper()\n','definition_missing'),
                                         ('value = 1\n','not_found')])
def test_claims_review_non_definition_preserves_each_lookup_route(tmp_path, other, source, reason):
    repo,revision=repository(tmp_path,{'logic.py':source,'other.py':other})
    report,_=make_case(tmp_path,'`logic.py` 함수 `moved_helper` 정의가 있다.')
    claim=atoms(run(report,repos=[str(repo)],commits={str(repo):revision}),'K4')[0]
    # The pre-p4-1 not_found path already accepted code mentions/key shapes.
    # Only the newly added definition_missing path requires a declaration.
    legacy_hit = reason == 'not_found' and other not in NON_DEFINITIONS[9:11]
    expected = ('unverifiable', 'found_outside_scope') if legacy_hit else ('absent', reason)
    assert (claim['verdict'],claim['reason_code']) == expected
    assert bool(claim['source'].get('repository_hits')) == legacy_hit


@pytest.mark.parametrize('other', [
    'def moved_helper(): pass\n', 'async def moved_helper(): pass\n',
    'class moved_helper: pass\n', 'moved_helper = 1\n',
    'moved_helper: int = 1\n', 'first = moved_helper = 1\n',
])
def test_claims_review_real_definition_still_qualifies(tmp_path, other):
    repo,revision=repository(tmp_path,{'logic.py':'moved_helper()\n','other.py':other})
    report,_=make_case(tmp_path,'`logic.py` 함수 `moved_helper` 정의가 있다.')
    claim=atoms(run(report,repos=[str(repo)],commits={str(repo):revision}),'K4')[0]
    assert claim['reason_code']=='found_outside_scope'
    assert claim['source']['repository_hit_kind']=='definition'


@pytest.mark.parametrize('other', [
    'const table = {"moved_helper": 1};\n',
    'const table = {moved_helper: 1};\n',
    'invoke({moved_helper: 1});\n',
    'if (moved_helper) { consume(); }\n',
    'const message = "def moved_helper(): pass";\n',
    'const matcher = /; moved_helper = /;\n',
    'function wrapper(\n    moved_helper = 1\n) {}\n',
])
def test_claims_review_javascript_non_definition_is_not_a_definition(tmp_path, other):
    repo,revision=repository(tmp_path,{'logic.py':'moved_helper()\n','other.js':other})
    report,_=make_case(tmp_path,'`logic.py` 함수 `moved_helper` 정의가 있다.')
    claim=atoms(run(report,repos=[str(repo)],commits={str(repo):revision}),'K4')[0]
    assert (claim['verdict'],claim['reason_code'])==('absent','definition_missing')


@pytest.mark.parametrize('other', [
    'export function moved_helper() {}\n',
    'class moved_helper {}\n',
    'const moved_helper = () => 1;\n',
    'let moved_helper = 1;\n',
    'function wrapper() {\n    function moved_helper() {}\n}\n',
    'describe("synthetic", () => {\n    const moved_helper = () => 1;\n});\n',
])
def test_claims_review_javascript_declaration_statement_context(tmp_path,other):
    repo,revision=repository(tmp_path,{'logic.py':'moved_helper()\n','other.js':other})
    report,_=make_case(tmp_path,'`logic.py` 함수 `moved_helper` 정의가 있다.')
    claim=atoms(run(report,repos=[str(repo)],commits={str(repo):revision}),'K4')[0]
    # An enclosing callback call keeps parentheses open: the strict path cannot
    # prove its declaration context. Ordinary top-level/block headers still work.
    expected = (('absent', 'definition_missing') if other.startswith('describe(')
                else ('unverifiable', 'found_outside_scope'))
    assert (claim['verdict'], claim['reason_code']) == expected
