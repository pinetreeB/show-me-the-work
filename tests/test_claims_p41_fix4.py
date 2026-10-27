"""Synthetic statement-boundary regressions for the strict JS/TS path."""
import pytest

from smtw.claims.check import run
from smtw.claims.model import Budget
from smtw.claims.sources import Repositories
from tests.claims_support import make_case
from tests.test_claims_p41 import atoms, repository


EXPRESSION_DECLARATIONS = [
    'consume(\n  const moved_helper = 1\n);\n',
    'const entries = [\n  const moved_helper = 1\n];\n',
    'const choice = ready ?\n  const moved_helper = 1\n  : fallback;\n',
    'const choice = ready ? first :\n  const moved_helper = 1;\n',
    'const value =\n  const moved_helper = 1;\n',
    'consume(\n  {\n    const moved_helper = 1;\n  }\n);\n',
    'const entries = [\n  {\n    const moved_helper = 1;\n  }\n];\n',
    'consume(\n  // );\n  const moved_helper = 1\n);\n',
    'const seed = 1 // ;\nconst moved_helper = 1;\n',
    ')\nconsume(\n{\nconst moved_helper = 1;\n}\n);\n',
]


@pytest.mark.parametrize('suffix', ['.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx'])
@pytest.mark.parametrize('text', EXPRESSION_DECLARATIONS)
def test_claims_expression_context_is_not_declaration_proof(suffix, text):
    repos = Repositories([], {}, {}, Budget(10))
    assert not repos._definition_sites(text, suffix, repos._mask_noncode(text, suffix), ['moved_helper'])


@pytest.mark.parametrize('suffix', ['.js', '.ts'])
@pytest.mark.parametrize('other', EXPRESSION_DECLARATIONS[:3])
@pytest.mark.parametrize('source,legacy', [('moved_helper()\n', False), ('value = 1\n', True)])
def test_claims_grok_expression_keeps_baseline_verdict(tmp_path, suffix, other, source, legacy):
    repo, revision = repository(tmp_path, {'logic.py': source, 'other' + suffix: other})
    report, _ = make_case(tmp_path, '`logic.py` 함수 `moved_helper` 정의가 있다.')
    result = run(report, repos=[str(repo)], commits={str(repo): revision})
    claim, = atoms(result, 'K4')
    expected = ('unverifiable', 'found_outside_scope') if legacy else ('absent', 'definition_missing')
    assert (claim['verdict'], claim['reason_code']) == expected
    assert bool(claim['source'].get('repository_hits')) == legacy
    assert result['status'] == ('INCOMPLETE' if legacy else 'FAIL')


@pytest.mark.parametrize('suffix', ['.js', '.ts'])
@pytest.mark.parametrize('other', [
    'export function moved_helper(input, config) { return input; }\n',
    'const moved_helper = 1;\n',
    'function wrapper() {\n  const moved_helper = 1;\n}\n',
    'if (ready) {\n  const moved_helper = 1;\n}\n',
    'const seed = 1;\n// comment\n\nexport const moved_helper = 1;\n',
    'function wrapper() {}\nconst moved_helper = 1;\n',
    'const r = /[()\\[\\]{}]/;\nconst moved_helper = 1;\n',
    'const text = "([{";\nconst moved_helper = 1;\n',
    'const text = `([{}])`;\n/* ([{ */\nconst moved_helper = 1;\n',
])
def test_claims_real_statement_declaration_still_qualifies(tmp_path, suffix, other):
    repo, revision = repository(tmp_path, {'logic.py': 'moved_helper()\n', 'other' + suffix: other})
    report, _ = make_case(tmp_path, '`logic.py` 함수 `moved_helper` 정의가 있다.')
    claim, = atoms(run(report, repos=[str(repo)], commits={str(repo): revision}), 'K4')
    assert (claim['verdict'], claim['reason_code']) == ('unverifiable', 'found_outside_scope')
    assert claim['source']['repository_hit_kind'] == 'definition'
