"""Synthetic Python-only definition fallback and prior review regressions."""
import pytest

from smtw.claims.check import run
from smtw.claims.model import Budget
from smtw.claims.sources import Repositories
from tests.claims_support import make_case
from tests.test_claims_p41 import atoms, git, repository


GROK_FIX4_CASES = [
    'const box = {\n  const moved_helper = 1;\n};\n',
    'const box = {\nfunction moved_helper() {}\n};\n',
    'export default {\nfunction moved_helper() {}\n};\n',
    'class Box {\n  const moved_helper = 1;\n}\n',
    'const box = {\n  a() {\n    return 1;\n  }\n  const moved_helper = 1\n};\n',
]


@pytest.mark.parametrize('other', GROK_FIX4_CASES)
@pytest.mark.parametrize('suffix', ['.js', '.ts'])
@pytest.mark.parametrize('source,legacy', [('moved_helper()\n', False), ('value = 1\n', True)])
def test_claims_grok_object_and_class_text_preserves_baseline(tmp_path, other, suffix, source, legacy):
    repo, revision = repository(tmp_path, {'logic.py': source, 'other' + suffix: other})
    report, _ = make_case(tmp_path, '`logic.py` 함수 `moved_helper` 정의가 있다.')
    result = run(report, repos=[str(repo)], commits={str(repo): revision})
    claim, = atoms(result, 'K4')
    expected = ('unverifiable', 'found_outside_scope') if legacy else ('absent', 'definition_missing')
    assert (claim['verdict'], claim['reason_code']) == expected
    assert bool(claim['source'].get('repository_hits')) == legacy
    assert result['status'] == ('INCOMPLETE' if legacy else 'FAIL')


PYTHON_BINDINGS = [
    ('moved_helper', 'def moved_helper(): pass\n'),
    ('moved_helper', 'async def moved_helper(): pass\n'),
    ('moved_helper', 'class moved_helper: pass\n'),
    ('moved_helper', 'moved_helper = 1\n'),
    ('moved_helper', 'moved_helper: int = 1\n'),
    ('moved_helper', 'first = moved_helper = 1\n'),
    ('moved_helper', 'first, moved_helper = values\n'),
    ('moved_helper', 'first, *moved_helper = values\n'),
    ('Box.member', 'Box.member = 1\n'),
    ('Box.member', 'class Box:\n    def member(self): pass\n'),
    ('Box.member', 'class Box:\n    async def member(self): pass\n'),
    ('Box.member', 'class Box:\n    member = 1\n'),
    ('Box.member', 'class Box:\n    member: int = 1\n'),
    ('Box.member', 'class Box:\n    first = member = 1\n'),
    ('Box.Inner', 'class Box:\n    class Inner: pass\n'),
    ('Box.Inner.member', 'class Box:\n    class Inner:\n        member = 1\n'),
]


@pytest.mark.parametrize('suffix', ['.py', '.pyi'])
@pytest.mark.parametrize('token,other', PYTHON_BINDINGS)
def test_claims_python_module_and_qualified_class_bindings(tmp_path, suffix, token, other):
    repo, revision = repository(tmp_path, {'logic.py': token + '()\n', 'other' + suffix: other})
    report, _ = make_case(tmp_path, f'`logic.py` 함수 `{token}` 정의가 있다.')
    claim, = atoms(run(report, repos=[str(repo)], commits={str(repo): revision}), 'K4')
    assert (claim['verdict'], claim['reason_code']) == ('unverifiable', 'found_outside_scope')
    assert claim['source']['repository_hits'][0]['path'] == 'other' + suffix
    assert claim['source']['repository_hit_kind'] == 'definition'


@pytest.mark.parametrize('token,other', [
    ('moved_helper', 'def outer():\n    def moved_helper(): pass\n'),
    ('moved_helper', 'def outer():\n    class moved_helper: pass\n'),
    ('moved_helper', 'if ready:\n    def moved_helper(): pass\n'),
    ('moved_helper', 'class Box:\n    def moved_helper(self): pass\n'),
    ('moved_helper', 'class Box:\n    moved_helper = 1\n'),
    ('Box.member', 'class Other:\n    member = 1\n'),
    ('Box.member', 'class Box:\n    def wrapper(self):\n        member = 1\n'),
    ('Box.member', 'class Box:\n    if ready:\n        member = 1\n'),
    ('moved_helper', 'moved_helper: int\n'),
    ('moved_helper', 'moved_helper =\n'),
    ('moved_helper', 'async def moved_helper(\n'),
    ('Box.member', 'class Box:\n    member =\n'),
])
def test_claims_python_unsupported_scope_or_parse_failure_stays_absent(tmp_path, token, other):
    repo, revision = repository(tmp_path, {'logic.py': token + '()\n', 'other.py': other})
    report, _ = make_case(tmp_path, f'`logic.py` 함수 `{token}` 정의가 있다.')
    claim, = atoms(run(report, repos=[str(repo)], commits={str(repo): revision}), 'K4')
    assert (claim['verdict'], claim['reason_code']) == ('absent', 'definition_missing')
    assert not claim['source'].get('repository_hits')


@pytest.mark.parametrize('first', ['legacy', 'strict'])
def test_claims_qualified_class_lookup_cannot_change_legacy_results(tmp_path, first):
    repo, revision = repository(tmp_path, {'logic.py': 'value = 1\n',
                                         'other.py': 'class Box:\n    member = 1\n'})
    repos = Repositories([str(repo)], {str(repo): revision}, {}, Budget(30))
    for mode in [first, 'strict' if first == 'legacy' else 'legacy', first]:
        result = repos.identifier('Box.member', other_definition='logic.py' if mode == 'strict' else '')
        assert result[1]
        assert result[0] == (mode == 'strict')
    report, _ = make_case(tmp_path, '`logic.py` 함수 `Box.member` 정의가 있다.')
    claim, = atoms(run(report, repos=[str(repo)], commits={str(repo): revision}), 'K4')
    assert (claim['verdict'], claim['reason_code']) == ('absent', 'not_found')


@pytest.mark.parametrize('suffix', ['.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx', '.go', '.rs', '.java', '.rb', '.txt', '.json'])
def test_claims_non_python_extension_never_supplies_new_definition(suffix):
    text = 'def moved_helper(): pass\n'
    repos = Repositories([], {}, {}, Budget(10))
    assert not repos._definition_sites(text, suffix, [text], ['moved_helper'])


@pytest.mark.parametrize('other', [
    'const text = `\nfunction moved_helper() {}\n`;\n',
    'consume(`\nconst moved_helper = 1\n`);\n',
    'React.createElement("pre", null, "function moved_helper() {}");\n',
    'const ok = a < b;\nfunction moved_helper() {}\n',
    'const moved_helper: Array<string> = 1;\n',
    'consume(\nfunction moved_helper() {}\n);\n',
])
def test_claims_prior_review_js_controls_are_deferred(tmp_path, other):
    repo, revision = repository(tmp_path, {'logic.py': 'moved_helper()\n', 'other.js': other})
    report, _ = make_case(tmp_path, '`logic.py` 함수 `moved_helper` 정의가 있다.')
    claim, = atoms(run(report, repos=[str(repo)], commits={str(repo): revision}), 'K4')
    assert (claim['verdict'], claim['reason_code']) == ('absent', 'definition_missing')


def test_claims_class_qualified_definition_uses_only_fixed_tree(tmp_path):
    repo, revision = repository(tmp_path, {'logic.py': 'Box.member()\nBox.later_member()\n',
                                         'other.py': 'class Box:\n    member = 1\n'})
    (repo / 'other.py').write_text('class Box:\n    later_member = 1\n', encoding='utf-8')
    git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qam', 'later synthetic state')
    report, _ = make_case(tmp_path, '`logic.py` 함수 `Box.member` 정의가 있다.\n'
                                   '`logic.py` 함수 `Box.later_member` 정의가 있다.')
    claims = atoms(run(report, repos=[str(repo)], commits={str(repo): revision}), 'K4')
    assert [(c['verdict'], c['reason_code']) for c in claims] == [
        ('unverifiable', 'found_outside_scope'), ('absent', 'definition_missing')]
    assert claims[0]['source']['repository_hits'][0]['revision'] == revision
