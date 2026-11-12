"""Offline synthetic parser boundaries; real Babel is an explicit test extra."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from smtw.claims.check import run
from smtw.claims import js_definitions as js
from tests.claims_support import make_case
from tests.test_claims_p41 import atoms, git, repository


@pytest.fixture
def parser(monkeypatch):
    node, package = os.environ.get('SMTW_TEST_JS_NODE'), os.environ.get('SMTW_TEST_JS_PACKAGE')
    if not node or not package:
        pytest.skip('optional pinned Babel/Node test paths not supplied')
    config = js.JSParserConfig(Path(node), Path(package))
    js.configured(config, [])  # Misconfigured supplied extras must fail, not skip.
    # These are AST/protocol correctness tests, not workstation load benchmarks.
    # Production deadlines are exercised separately with a controlled clock.
    monkeypatch.setattr(js, 'TOTAL_SECONDS', 30.0)
    monkeypatch.setattr(js, 'FILE_SECONDS', 5.0)
    return config


def case(tmp_path, other, suffix='.mjs', *, extra=None, token='moved_helper'):
    repo, revision = repository(tmp_path, {
        'logic.py': token + '()\n', 'blank.py': 'value = 1\n', 'other' + suffix: other,
        **(extra or {}),
    })
    report, _ = make_case(tmp_path, f'`logic.py` 함수 `{token}` 정의가 있다.\n'
                                  f'`blank.py` 함수 `{token}` 정의가 있다.')
    return report, dict(repos=[str(repo)], commits={str(repo): revision})


def projected(result):
    result = deepcopy(result)
    result.pop('elapsed_seconds', None)
    return result


POSITIVE = [
    'function moved_helper() {}', 'async function moved_helper() {}',
    'function* moved_helper() {}', 'class moved_helper {}',
    'const moved_helper = 1;', 'let moved_helper = () => 1;', 'var moved_helper = 1;',
    'export function moved_helper() {}', 'export const moved_helper = 1;',
    'export class moved_helper {}', 'export default function moved_helper() {}',
    'export default class moved_helper {}',
    'const seed = 1 // ;\nconst moved_helper = 1;',
    'const matcher = /[(){}\\[\\]]/;\nconst moved_helper = 1;',
    'const text = `function absent() {}`;\nfunction moved_helper() {}',
    'const ok = a < b;\nfunction moved_helper() {}',
]


@pytest.mark.parametrize('suffix', ['.mjs', '.ts'])
@pytest.mark.parametrize('other', POSITIVE)
def test_claims_p42_real_module_binding(parser, tmp_path, suffix, other):
    report, kwargs = case(tmp_path, other, suffix)
    baseline = run(report, **kwargs)
    result = run(report, **kwargs, js_parser=parser)
    first, legacy = atoms(result, 'K4')
    assert (first['verdict'], first['reason_code']) == ('unverifiable', 'found_outside_scope')
    assert first['source']['repository_hits'][0]['parser_version'] == js.VERSION
    assert legacy == atoms(baseline, 'K4')[1]
    assert not result['timed_out']


NEGATIVE = [
    'consume(\nconst moved_helper = 1\n);',
    'const box = {\nconst moved_helper = 1;\n};',
    'class Box {\nconst moved_helper = 1;\n}',
    'export default {\nfunction moved_helper() {}\n};',
    'const rows = [\nconst moved_helper = 1\n];',
    'ready ?\nconst moved_helper = 1\n: fallback;',
    'function wrapper() { return /}/;\nmoved_helper = 1; }',
    'const r = /"/;\n/* "\nclass moved_helper {}\n" */',
    'const s = `${flag ? `a` : `\nconst moved_helper = 1;\n`}`;',
    'const view = <pre>function moved_helper() {}</pre>;',
    'declare class moved_helper {}', 'abstract class moved_helper {}',
    'export declare function moved_helper(): void;',
    'export declare const moved_helper: number;', 'declare const moved_helper = 1;',
    'function moved_helper(x: number): void;',
    'using moved_helper = getResource();', 'await using moved_helper = getResource();',
    'const internal = 1; export { internal as moved_helper };',
    'export { moved_helper } from "./a.js";', 'export type { moved_helper };',
    'export default moved_helper;', 'export default function() {}',
    'const { moved_helper } = obj;', 'const { key: moved_helper = 1 } = obj;',
    'const [moved_helper] = arr;', 'const other = function moved_helper() {};',
    'const Box = class moved_helper {};',
    'namespace Box { export function moved_helper() {} }',
    'enum moved_helper { A = 1 }', 'interface moved_helper { a: number }',
    'type moved_helper = string;',
    'exports.moved_helper = function() {};', 'module.exports = { moved_helper() {} };',
    'function moved_helper() {}\nconst broken = ;',
    'function outer() { function moved_helper() {} }',
    'if (ready) { const moved_helper = 1; }',
    'class Box { moved_helper() {} }', 'let moved_helper;',
    'const moved_\\u0068elper = 1;',
]


@pytest.mark.parametrize('suffix', ['.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx', '.mts', '.cts'])
@pytest.mark.parametrize('other', NEGATIVE)
def test_claims_p42_no_new_rescue_from_non_binding(parser, tmp_path, suffix, other):
    report, kwargs = case(tmp_path, other, suffix)
    before = run(report, **kwargs)
    assert atoms(before, 'K4')[0]['reason_code'] == 'definition_missing'
    assert projected(run(report, **kwargs, js_parser=parser)) == projected(before)


@pytest.mark.parametrize('suffix', ['.d.ts', '.d.mts', '.d.cts', '.D.TS'])
@pytest.mark.parametrize('other', ['export function moved_helper(): void;', 'export const moved_helper = 1;'])
def test_claims_p42_declaration_suffix_excluded(parser, tmp_path, suffix, other):
    report, kwargs = case(tmp_path, other, suffix)
    assert projected(run(report, **kwargs, js_parser=parser)) == projected(run(report, **kwargs))


@pytest.mark.parametrize('suffix,metadata,positive', [
    ('.js', None, False), ('.jsx', None, False), ('.cjs', '{"type":"module"}', False),
    ('.js', '{"type":"module"}', True), ('.jsx', '{"type":"module"}', True),
    ('.js', '{"type":"commonjs"}', False), ('.js', '{}', False),
    ('.js', '{"type":"unknown"}', False), ('.js', 'broken', False),
    ('.js', '[]', False), ('.mjs', None, True), ('.mts', None, True),
])
def test_claims_p42_source_goal_is_fixed_tree_metadata(parser, tmp_path, suffix, metadata, positive):
    extra = {'package.json': metadata} if metadata is not None else {}
    report, kwargs = case(tmp_path, 'import value from "./dep.js";\nfunction moved_helper() {}',
                          suffix, extra=extra)
    result = run(report, **kwargs, js_parser=parser)
    assert atoms(result, 'K4')[0]['verdict'] == ('unverifiable' if positive else 'absent')


@pytest.mark.parametrize('token,other,positive', [
    ('Box', 'const Box = class moved_helper {};', True),
    ('moved_helper', 'const Box = class moved_helper {};', False),
    ('moved_helper', 'function moved_helper(x: number): void;\nfunction moved_helper(x: number) {}', True),
    ('moved_helper', 'function moved_helper(x: number): void;', False),
    ('moved_helper', 'const moved_helper: Array<string> = 1;', True),
])
def test_claims_p42_review_named_expression_and_overload(parser, tmp_path, token, other, positive):
    report, kwargs = case(tmp_path, other, '.ts', token=token)
    assert atoms(run(report, **kwargs, js_parser=parser), 'K4')[0]['verdict'] == (
        'unverifiable' if positive else 'absent')


@pytest.mark.parametrize('literal', [
    'reason = "moved_helper"', 'messages = ["moved_helper"]',
    'text = "prefix moved_helper suffix"', 'text = "moved_" + "helper"',
    'text = "def moved_helper(): pass"', '__all__ = ["moved_helper"]',
])
def test_claims_p42_d2_remains_unchanged(parser, tmp_path, literal):
    report, kwargs = case(tmp_path, literal, '.py')
    assert projected(run(report, **kwargs, js_parser=parser)) == projected(run(report, **kwargs))


@pytest.mark.parametrize('setting,value', [('MAX_TREE', 1), ('MAX_FILES', 0), ('MAX_BLOB', 1),
                                         ('MAX_BYTES', 1), ('MAX_SEARCH', 1), ('MAX_TOKENS', 0),
                                         ('TOTAL_SECONDS', 0), ('MAX_HITS', 0), ('MAX_OUTPUT', 1)])
def test_claims_p42_limits_preserve_complete_result(parser, tmp_path, monkeypatch, setting, value):
    report, kwargs = case(tmp_path, 'export function moved_helper() {}')
    before = run(report, **kwargs)
    monkeypatch.setattr(js, setting, value)
    assert projected(run(report, **kwargs, js_parser=parser)) == projected(before)


def test_claims_p42_common_token_does_not_cancel_rare_token(parser, tmp_path):
    files = {f'noise/{index}.js': '// common_tok\n' for index in range(257)}
    files.update({'logic.py': 'common_tok()\nmoved_helper()\n',
                  'other.mjs': 'export function moved_helper() {}'})
    repo, revision = repository(tmp_path, files)
    report, _ = make_case(tmp_path, '`logic.py` 함수 `common_tok` 정의가 있다.\n'
                                   '`logic.py` 함수 `moved_helper` 정의가 있다.')
    result = run(report, repos=[str(repo)], commits={str(repo): revision}, js_parser=parser)
    assert [(c['verdict'], c['reason_code']) for c in atoms(result, 'K4')] == [
        ('absent', 'definition_missing'), ('unverifiable', 'found_outside_scope')]


def test_claims_p42_fixed_tree_and_nearest_package(parser, tmp_path):
    report, kwargs = case(tmp_path, 'export function moved_helper() {}', '.js',
                          extra={'package.json': '{"type":"commonjs"}',
                                 'nested/package.json': '{"type":"module"}',
                                 'nested/code.js': 'export function moved_helper() {}'})
    repo = Path(kwargs['repos'][0])
    (repo / 'nested/code.js').write_text('const value = 1;', encoding='utf-8')
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'later')
    hit = atoms(run(report, **kwargs, js_parser=parser), 'K4')[0]['source']['repository_hits'][0]
    assert hit['path'] == 'nested/code.js'
    assert hit['revision'] == kwargs['commits'][str(repo)]


@pytest.mark.parametrize('bad', ['missing', 'relative', 'wrapper'])
def test_claims_p42_invalid_configuration_never_changes_absence(tmp_path, bad):
    report, kwargs = case(tmp_path, 'export function moved_helper() {}')
    node = tmp_path / ('node.cmd' if bad == 'wrapper' else 'node.exe')
    if bad == 'relative':
        node = Path('node')
    config = js.JSParserConfig(node, tmp_path / 'missing-package')
    assert projected(run(report, **kwargs, js_parser=config)) == projected(run(report, **kwargs))


@pytest.mark.parametrize('error', [OSError('synthetic'), subprocess.TimeoutExpired('synthetic', 1),
                                 ValueError('synthetic'), RuntimeError('synthetic')])
def test_claims_p42_optional_exception_cannot_reach_legacy_timeout(tmp_path, monkeypatch, error):
    report, kwargs = case(tmp_path, 'export function moved_helper() {}')
    before = run(report, **kwargs)
    def broken(*args):
        raise error
    monkeypatch.setattr(js, 'prove', broken)
    result = run(report, **kwargs, js_parser=js.JSParserConfig(tmp_path/'node.exe', tmp_path/'package'))
    assert projected(result) == projected(before)
    assert not result['timed_out']


@pytest.mark.parametrize('fault', ['crash', 'stall', 'malformed', 'flood', 'bad_id', 'after_hit_crash', 'trailing'])
def test_claims_p42_real_worker_failures_preserve_result(parser, tmp_path, monkeypatch, fault):
    report, kwargs = case(tmp_path, 'export function moved_helper() {}')
    before = run(report, **kwargs)
    ready = 'console.log(JSON.stringify({ready:true,version:"8.0.8",policy:"module-bindings-v1"}));\n'
    faults = {
        'crash': 'process.exit(3);',
        'stall': ready + 'setInterval(()=>{},1000);',
        'malformed': ready + 'console.log("not json");',
        'flood': ready + 'process.stdout.write("x".repeat(5*1024*1024));',
        'bad_id': ready + 'console.log(JSON.stringify({id:-1,oid:"bad",parsed:true,hits:[]}));',
        'after_hit_crash': ready + '''
          const {createInterface}=await import('node:readline');
          for await (const line of createInterface({input:process.stdin})) {
            const r=JSON.parse(line), start=r.text.indexOf('moved_helper');
            console.log(JSON.stringify({id:r.id,oid:r.oid,parsed:true,hits:[{
              name:'moved_helper',line:1,start,end:start+12,kind:'FunctionDeclaration'}]}));
            process.exit(3);
          }
        ''',
        'trailing': ready + '''
          const {createInterface}=await import('node:readline');
          for await (const line of createInterface({input:process.stdin})) {
            const r=JSON.parse(line), start=r.text.indexOf('moved_helper');
            const reply={id:r.id,oid:r.oid,parsed:true,hits:[{
              name:'moved_helper',line:1,start,end:start+12,kind:'FunctionDeclaration'}]};
            process.stdout.write(JSON.stringify(reply)+'\\nextra\\n',()=>process.exit(0));
          }
        ''',
    }
    worker = tmp_path / 'synthetic-worker.mjs'
    worker.write_text(faults[fault], encoding='utf-8')
    monkeypatch.setattr(js, 'WORKER', worker)
    monkeypatch.setattr(js, 'FILE_SECONDS', 0.5)
    children = []
    original_child = js.Child

    class ObservedChild(original_child):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            children.append(self)

    monkeypatch.setattr(js, 'Child', ObservedChild)
    result = run(report, **kwargs, js_parser=parser)
    assert projected(result) == projected(before)
    assert children and all(child.process.poll() is not None for child in children)


def test_claims_p42_node_environment_is_not_inherited(parser, tmp_path, monkeypatch):
    report, kwargs = case(tmp_path, 'export function moved_helper() {}')
    monkeypatch.setenv('NODE_OPTIONS', '--definitely-not-a-node-option')
    monkeypatch.setenv('NODE_PATH', str(tmp_path / 'untrusted'))
    result = run(report, **kwargs, js_parser=parser)
    assert atoms(result, 'K4')[0]['reason_code'] == 'found_outside_scope'


@pytest.mark.parametrize('other', ['// 한글\r\nconst moved_helper: number = 1;',
                                 '\ufeffconst moved_helper: number = 1;',
                                 '// note\rconst moved_helper = 1;'])
def test_claims_p42_byte_spans_and_line_endings(parser, tmp_path, other):
    report, kwargs = case(tmp_path, other, '.ts')
    assert atoms(run(report, **kwargs, js_parser=parser), 'K4')[0]['reason_code'] == 'found_outside_scope'


def test_claims_p42_malformed_package_is_file_local(parser, tmp_path):
    report, kwargs = case(tmp_path, 'export function moved_helper() {}', extra={
        'broken/package.json': '{"type": []}', 'broken/file.js': 'function moved_helper() {}'})
    result = run(report, **kwargs, js_parser=parser)
    assert atoms(result, 'K4')[0]['source']['repository_hits'][0]['path'] == 'other.mjs'


def test_claims_p42_metadata_limit_preserves_result(parser, tmp_path, monkeypatch):
    report, kwargs = case(tmp_path, 'export function moved_helper() {}', '.js',
                          extra={'package.json': '{"type":"module"}'})
    before = run(report, **kwargs)
    monkeypatch.setattr(js, 'MAX_METADATA', 1)
    assert projected(run(report, **kwargs, js_parser=parser)) == projected(before)


def test_claims_p42_unknown_git_wrapper_is_not_launched(parser, tmp_path, monkeypatch):
    report, kwargs = case(tmp_path, 'export function moved_helper() {}')
    before = run(report, **kwargs)
    monkeypatch.setattr(js.shutil, 'which', lambda name: None)
    assert projected(run(report, **kwargs, js_parser=parser)) == projected(before)


def test_claims_p42_worker_participates_in_code_identity(tmp_path, monkeypatch):
    from smtw.claims import code_identity
    monkeypatch.setattr(code_identity, '__file__', str(tmp_path / 'code_identity.py'))
    (tmp_path / 'code_identity.py').write_text('synthetic', encoding='utf-8')
    worker = tmp_path / 'js_definitions.mjs'
    worker.write_text('first', encoding='utf-8')
    first = code_identity.code_sha256()
    worker.write_text('second', encoding='utf-8')
    assert code_identity.code_sha256() != first


def test_claims_p42_unicode_repository_and_blob_path(parser, tmp_path):
    folder = tmp_path / '공백 저장소'
    folder.mkdir()
    report, kwargs = case(folder, 'const unrelated = 1;', '.js', extra={
        '한글 경로/정의.mjs': 'export function moved_helper() {}'})
    claim = atoms(run(report, **kwargs, js_parser=parser), 'K4')[0]
    assert claim['source']['repository_hits'][0]['path'] == '한글 경로/정의.mjs'


def test_claims_p42_no_fixed_commit_means_no_new_proof(parser, tmp_path):
    report, kwargs = case(tmp_path, 'export function moved_helper() {}')
    kwargs.pop('commits')
    assert projected(run(report, **kwargs, js_parser=parser)) == projected(run(report, **kwargs))


def test_claims_p42_repeated_claim_proofs_obey_total_hit_limit(parser, tmp_path, monkeypatch):
    report, kwargs = case(tmp_path, 'export function moved_helper() {}')
    report.write_text('`logic.py` 함수 `moved_helper` 정의가 있다.\n'*4, encoding='utf-8')
    before = run(report, **kwargs)
    monkeypatch.setattr(js, 'MAX_HITS', 2)
    assert projected(run(report, **kwargs, js_parser=parser)) == projected(before)


def test_claims_p42_cli_explicit_paths(parser, tmp_path):
    report, kwargs = case(tmp_path, 'export function moved_helper() {}')
    repo = kwargs['repos'][0]
    # Carry the functional-test budget into the child without adding a product
    # flag or environment override. The actual CLI still parses every argument.
    bootstrap = ('import runpy; from smtw.claims import js_definitions as js; '
                 'js.TOTAL_SECONDS=30.0; js.FILE_SECONDS=5.0; '
                 'runpy.run_module("smtw", run_name="__main__")')
    result = subprocess.run([sys.executable, '-c', bootstrap, 'claims', 'check', '--report', str(report),
        '--repo', repo, '--commit', repo + '=' + kwargs['commits'][repo], '--format', 'json',
        '--js-node', str(parser.node), '--js-parser', str(parser.package)], capture_output=True)
    data = json.loads(result.stdout.decode('utf-8'))
    assert result.returncode == 3
    assert atoms(data, 'K4')[0]['source']['repository_hits'][0]['parser_version'] == js.VERSION
