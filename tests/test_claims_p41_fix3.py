"""Synthetic compatibility checks for legacy mentions and strict definitions."""
import pytest

from smtw.claims.check import run
from smtw.claims.model import Budget
from smtw.claims.sources import Repositories
from tests.claims_support import make_case
from tests.test_claims_p41 import atoms, repository


LEGACY_CASES = [
    ('PascalEvent', {'settings.json': '{"PascalEvent": []}\n',
                     'README.md': 'PascalEvent\n'}, False),
    ('PascalEvent', {'settings.json': '{"PascalEvent": []}\n',
                     'README.md': 'PascalEvent\n',
                     'events.py': 'events = {"PascalEvent": []}\n'}, True),
    ('snake_hook', {'snake_hook.py': 'value = 1\n',
                    'settings.json': '{"path": "hooks/snake_hook.py"}\n',
                    'README.md': 'hooks/snake_hook.py\n'}, False),
    ('snake_hook', {'snake_hook.py': 'value = 1\n',
                    'settings.json': '{"path": "hooks/snake_hook.py"}\n',
                    'README.md': 'hooks/snake_hook.py\n',
                    'loader.py': 'from snake_hook import main\n'}, True),
    ('invented_name', {'other.py': 'value = 1\n'}, False),
]

FIX2_REVIEW_CASES = [
    ('agy-P1', '.py', 'def wrapper():\n    moved_helper = 1\n'),
    ('agy-P2', '.py', 'class Wrapper:\n    moved_helper = 1\n'),
    ('agy-P3', '.py', 'if (moved_helper := 1):\n    pass\n'),
    ('agy-J1', '.js', 'const r = /"/;\n/* "\nclass moved_helper {}\n" */\n'),
    ('agy-J2', '.js', 'const r = /`/;\nconst s = `\nfunction moved_helper() {}\n`;\n'),
    ('agy-J3', '.js', 'const s = `${flag ? `a` : `\nconst moved_helper = 1;\n`}`;\n'),
    ('grok-JSX-function', '.jsx', 'export function Page() {\n  return (\n    <pre>\n      function moved_helper() {}\n    </pre>\n  );\n}\n'),
    ('grok-JSX-const', '.jsx', 'export function Page() {\n  return (\n    <pre>\n      const moved_helper = 1;\n    </pre>\n  );\n}\n'),
    ('grok-regex-newline', '.js', 'const pattern = /\nconst moved_helper = 1\n/;\n'),
]


@pytest.mark.parametrize('case,suffix,other', FIX2_REVIEW_CASES, ids=[row[0] for row in FIX2_REVIEW_CASES])
@pytest.mark.parametrize('source,legacy', [('moved_helper()\n', False), ('value = 1\n', True)])
def test_claims_fix2_review_preserves_baseline_and_blocks_new_rescue(tmp_path, case, suffix, other, source, legacy):
    repo, revision = repository(tmp_path, {'logic.py': source, 'other' + suffix: other})
    report, _ = make_case(tmp_path, '`logic.py` 함수 `moved_helper` 정의가 있다.')
    result = run(report, repos=[str(repo)], commits={str(repo): revision})
    claim, = atoms(result, 'K4')
    expected = ('unverifiable', 'found_outside_scope') if legacy else ('absent', 'definition_missing')
    assert (claim['verdict'], claim['reason_code']) == expected
    assert bool(claim['source'].get('repository_hits')) == legacy
    assert result['status'] == ('INCOMPLETE' if legacy else 'FAIL')


@pytest.mark.parametrize('suffix,other,defined', [
    ('.py', 'async def wrapper():\n    moved_helper = 1\n', False),
    ('.py', 'def wrapper():\n    moved_helper: int = 1\n', False),
    ('.py', 'class Wrapper:\n    moved_helper: int = 1\n', False),
    ('.py', 'def wrapper():\n    class Inner:\n        moved_helper = 1\n', False),
    ('.py', 'if ready:\n    moved_helper = 1\n', False),
    ('.py', 'try:\n    moved_helper: int = 1\nexcept Exception:\n    pass\n', False),
    ('.mjs', "const r = /'/;\n/* '\nclass moved_helper {}\n' */\n", False),
    ('.cjs', 'const r = /["/]/;\n/* "\nfunction moved_helper() {}\n" */\n', False),
    ('.ts', 'const r = /`/;\nconst s = `\nconst moved_helper = 1;\n`;\n', False),
    ('.tsx', 'const view = <pre>\nclass moved_helper extends Base\n</pre>;\n', False),
    ('.js', 'const ratio = left / right /* comment\nfunction moved_helper() {}\n*/;\n', False),
    ('.jsx', 'const s = "\nconst moved_helper = 1;\n";\n', False),
    ('.js', 'const r = /"/;\nfunction moved_helper() {}\n', False),
    ('.ts', 'const r = /[}]/;\nexport const moved_helper = 1;\n', False),
    ('.jsx', 'export function moved_helper() {\n  return <pre>text</pre>;\n}\n', False),
    ('.js', 'function moved_helper() {}\nconst text = `${value}`;\n', False),
])
def test_claims_declaration_context_controls(tmp_path, suffix, other, defined):
    repo, revision = repository(tmp_path, {'logic.py': 'moved_helper()\n', 'other' + suffix: other})
    report, _ = make_case(tmp_path, '`logic.py` 함수 `moved_helper` 정의가 있다.')
    claim, = atoms(run(report, repos=[str(repo)], commits={str(repo): revision}), 'K4')
    expected = ('unverifiable', 'found_outside_scope') if defined else ('absent', 'definition_missing')
    assert (claim['verdict'], claim['reason_code']) == expected


@pytest.mark.parametrize('token,files,found', LEGACY_CASES)
def test_claims_pre_p41_code_mention_scope_is_preserved(tmp_path, token, files, found):
    repo, revision = repository(tmp_path, {'logic.py': 'value = 1\n', **files})
    report, _ = make_case(tmp_path, f'`logic.py` 함수 `{token}` 정의가 있다.')
    claim, = atoms(run(report, repos=[str(repo)], commits={str(repo): revision}), 'K4')
    expected = ('unverifiable', 'found_outside_scope') if found else ('absent', 'not_found')
    assert (claim['verdict'], claim['reason_code']) == expected


@pytest.mark.parametrize('suffix', ['.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx'])
@pytest.mark.parametrize('declaration', [
    'export function camelName(input, config) { return input; }',
    'function camelName(input) { return input; }',
    'export const camelName = (input) => input;',
    'class camelName {}',
])
@pytest.mark.parametrize('source', ['value = 1\n', 'camelName()\n'])
def test_claims_explicit_js_ts_definition_keeps_only_legacy_route(tmp_path, suffix, declaration, source):
    repo, revision = repository(tmp_path, {
        'logic.py': source,
        'core' + suffix: declaration + '\n',
        'client' + suffix: "import { camelName } from './core" + suffix + "';\ncamelName(1);\n",
    })
    report, _ = make_case(tmp_path, '`logic.py` 함수 `camelName` 정의가 있다.')
    claim, = atoms(run(report, repos=[str(repo)], commits={str(repo): revision}), 'K4')
    if source == 'value = 1\n':
        assert (claim['verdict'], claim['reason_code']) == ('unverifiable', 'found_outside_scope')
        assert claim['source']['repository_hits'][0]['path'] == 'core' + suffix
        assert claim['source']['repository_hit_kind'] == 'definition'
    else:
        assert (claim['verdict'], claim['reason_code']) == ('absent', 'definition_missing')
        assert not claim['source'].get('repository_hits')


@pytest.mark.parametrize('first', ['legacy', 'strict'])
@pytest.mark.parametrize('other', [
    'some_call(moved_helper=1)\n', 'table = {"moved_helper": 1}\n',
])
def test_claims_legacy_lookup_cannot_contaminate_definition_cache(tmp_path, first, other):
    repo, revision = repository(tmp_path, {'logic.py': 'moved_helper()\n', 'other.py': other})
    repos = Repositories([str(repo)], {str(repo): revision}, {}, Budget(30))
    order = [first, 'strict' if first == 'legacy' else 'legacy', first]
    for mode in order:
        result = repos.identifier('moved_helper', other_definition='logic.py' if mode == 'strict' else '')
        assert result[1]
        assert result[0] == (mode == 'legacy')
    report, _ = make_case(tmp_path, '`logic.py` 함수 `moved_helper` 정의가 있다.')
    claim, = atoms(run(report, repos=[str(repo)], commits={str(repo): revision}), 'K4')
    assert (claim['verdict'], claim['reason_code']) == ('absent', 'definition_missing')
