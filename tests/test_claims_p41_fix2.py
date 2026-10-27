"""Synthetic JS/TS scope-confusion regressions from the fix1 review."""
import pytest

from smtw.claims.check import run
from smtw.claims.model import Budget
from smtw.claims.sources import Repositories
from tests.claims_support import make_case
from tests.test_claims_p41 import atoms, repository


# The first source is the exact synthetic Grok reproduction. Other rows guard
# regex character classes/escapes and strings, templates, comments, and scope.
BARE_ASSIGNMENTS = [
    'function wrapper() {\n    return /}/;\n    moved_helper = 1\n}\n',
    'function wrapper() {\n    return /[}]/;\n    moved_helper = 1\n}\n',
    'function wrapper() {\n    return /\\}/;\n    moved_helper = 1\n}\n',
    'function wrapper() {\n    return "}";\n    moved_helper = 1\n}\n',
    'function wrapper() {\n    return `}`;\n    moved_helper = 1\n}\n',
    'function wrapper() {\n    // }\n    moved_helper = 1\n}\n',
    'function wrapper() {\n    moved_helper = 1\n}\n',
    'moved_helper = 1;\n',
]


@pytest.mark.parametrize('suffix', ['.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx'])
@pytest.mark.parametrize('text', BARE_ASSIGNMENTS)
def test_claims_js_ts_bare_assignment_has_no_definition_proof(suffix, text):
    repos = Repositories([], {}, {}, Budget(10))
    sites = repos._definition_sites(text, suffix, repos._mask_noncode(text, suffix), ['moved_helper'])
    assert not sites


@pytest.mark.parametrize('suffix', ['.js', '.ts'])
@pytest.mark.parametrize('source,reason', [('moved_helper()\n', 'definition_missing'),
                                         ('value = 1\n', 'not_found')])
@pytest.mark.parametrize('other', [BARE_ASSIGNMENTS[0], BARE_ASSIGNMENTS[3], BARE_ASSIGNMENTS[-1]])
def test_claims_grok_scope_bypass_remains_absent(tmp_path, suffix, source, reason, other):
    repo, revision = repository(tmp_path, {'logic.py': source, 'other'+suffix: other})
    report, _ = make_case(tmp_path, '`logic.py` 함수 `moved_helper` 정의가 있다.')
    result = run(report, repos=[str(repo)], commits={str(repo): revision})
    claim, = atoms(result, 'K4')
    assert (claim['verdict'], claim['reason_code']) == ('absent', reason)
    assert not claim['source'].get('repository_hits')
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('declaration', [
    'function moved_helper() {}', 'class moved_helper {}',
    'const moved_helper = () => 1;', 'let moved_helper = 1;', 'var moved_helper = 1;',
])
@pytest.mark.parametrize('suffix', ['.js', '.ts'])
def test_claims_explicit_declaration_does_not_depend_on_brace_count(suffix, declaration):
    text = 'function wrapper() {\n    const pattern = /}/;\n    '+declaration+'\n}\n'
    repos = Repositories([], {}, {}, Budget(10))
    sites = repos._definition_sites(text, suffix, repos._mask_noncode(text, suffix), ['moved_helper'])
    assert sites == {('moved_helper', 3)}
