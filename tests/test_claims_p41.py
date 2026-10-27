"""Synthetic p4-1 regressions; no model calls or recorded report inputs."""
import subprocess

import pytest

from smtw.claims.check import run
from smtw.claims.model import Budget
from smtw.claims.sources import Repositories
from tests.claims_support import agy_call, generic, make_case, view


def atoms(result, kind):
    return [claim for claim in result['claims'] if claim['check_type'] == kind]


def inspect(tmp_path, text, source='if a:\n', **kwargs):
    report, transcript = make_case(tmp_path, text, [
        agy_call('view_file', AbsolutePath='C:/synthetic/logic.py'),
        view('C:/synthetic/logic.py', source),
    ])
    return run(report, agy=str(transcript), **kwargs)


@pytest.mark.parametrize('label', [
    '요청 처리/응답 정리/오류 안내', '요청 처리, 응답 정리, 오류 안내',
    '3항목', '3 items', '요청 처리/응답 정리 2항목',
])
def test_claims_summary_tail_is_unverifiable_label(tmp_path, label):
    result = inspect(tmp_path, f'`logic.py:1` `{label}`')
    claim, = [c for c in result['claims'] if c['reason_code'] == 'summary_label']
    assert (claim['verdict'], claim['reason_code']) == ('unverifiable', 'summary_label')
    assert atoms(result, 'K1')[0]['reason_code'] == 'range_only'


@pytest.mark.parametrize('quote', [
    'if a and b:', 'if a or b:', 'call(alpha, beta)',
    'alpha / beta / gamma', 'alpha, beta, gamma',
    'git status / git diff / git log',
    'request parsing / response formatting / error reporting',
    'request parsing, response formatting, error reporting',
    'request parsing / response formatting 2 items',
    'request_parser / response_formatter / error_handler',
    'requestParser / responseFormatter / errorHandler',
    '요청 처리 / 응답 정리 / call()', '"요청 처리", "응답 정리"',
])
def test_claims_ambiguous_or_changed_code_still_mismatches(tmp_path, quote):
    claim = atoms(inspect(tmp_path, f'`logic.py:1` `{quote}`'), 'K1')[0]
    assert (claim['verdict'], claim['reason_code']) == ('mismatch', 'quote_mismatch')


def test_claims_real_code_quote_still_matches(tmp_path):
    claim = atoms(inspect(tmp_path, '`logic.py:1` `if a and b:`', 'if a and b:\n'), 'K1')[0]
    assert (claim['verdict'], claim['reason_code']) == ('match', 'quote_match')


def test_claims_explicit_literal_prose_is_not_a_summary_label(tmp_path):
    claim = atoms(inspect(tmp_path, '`logic.py:1` 원문 `요청 처리/응답 정리/오류 안내`'), 'K1')[0]
    assert claim['reason_code'] == 'quote_mismatch'


def test_claims_label_does_not_suppress_next_code_quote(tmp_path):
    result = inspect(tmp_path, '`logic.py:1` `3항목` `if a and b:`')
    assert any(c['reason_code'] == 'quote_mismatch' for c in atoms(result, 'K1'))


@pytest.mark.parametrize('quote', ['request_parser / response_formatter',
                                 'requestParser / responseFormatter'])
def test_claims_ambiguous_short_expression_keeps_legacy_range_only(tmp_path, quote):
    claim = atoms(inspect(tmp_path, f'`logic.py:1` `{quote}`'), 'K1')[0]
    assert claim['reason_code'] == 'range_only'


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True).stdout.decode('utf-8').strip()


def repository(tmp_path, files):
    repo = tmp_path/'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    for name, content in files.items():
        path = repo/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'synthetic fixture')
    return repo, git(repo, 'rev-parse', 'HEAD')


@pytest.mark.parametrize('label,kind,verdict,reason', [
    ('tree', 'tree', 'match', 'object_exists'),
    ('TREE', 'tree', 'match', 'object_exists'),
    ('blob', 'blob', 'match', 'object_exists'),
    ('BLOB', 'blob', 'match', 'object_exists'),
    ('tree', 'blob', 'mismatch', 'object_kind_mismatch'),
    ('blob', 'tree', 'mismatch', 'object_kind_mismatch'),
    ('tree', 'commit', 'mismatch', 'object_kind_mismatch'),
    ('', 'tree', 'unverifiable', 'object_kind'),
    ('', 'blob', 'unverifiable', 'object_kind'),
    ('', 'commit', 'match', 'commit_exists'),
    ('commit', 'blob', 'mismatch', 'not_commit'),
    ('커밋', 'blob', 'mismatch', 'not_commit'),
])
@pytest.mark.parametrize('short', [False, True])
def test_claims_sha_checks_declared_object_kind(tmp_path, label, kind, verdict, reason, short):
    repo, revision = repository(tmp_path, {'logic.py': 'value = 1\n'})
    sha = {'commit': revision, 'tree': git(repo, 'rev-parse', 'HEAD^{tree}'),
           'blob': git(repo, 'rev-parse', 'HEAD:logic.py')}[kind]
    if short:
        sha = sha[:10]
    report, _ = make_case(tmp_path, f'repository `repo` {label} `{sha}`')
    claim = atoms(run(report, repos=[str(repo)]), 'K3')[0]
    assert (claim['verdict'], claim['reason_code']) == (verdict, reason)


@pytest.mark.parametrize('label', ['', 'tree', 'blob'])
def test_claims_missing_sha_keeps_absent(tmp_path, label):
    repo, _ = repository(tmp_path, {'logic.py': 'value = 1\n'})
    report, _ = make_case(tmp_path, f'repository `repo` {label} `{"f"*40}`')
    claim = atoms(run(report, repos=[str(repo)]), 'K3')[0]
    assert (claim['verdict'], claim['reason_code']) == ('absent', 'commit_not_found')


def test_claims_kind_binding_is_per_sha_and_not_git_output_echo(tmp_path):
    repo, revision = repository(tmp_path, {'logic.py': 'value = 1\n'})
    tree = git(repo, 'rev-parse', 'HEAD^{tree}')
    blob = git(repo, 'rev-parse', 'HEAD:logic.py')
    report, transcript = make_case(tmp_path, f'repository `repo` TREE `{tree}`, blob `{blob}`, `{revision}`', [
        agy_call('run_command', CommandLine=f'git rev-parse {tree}'), generic(tree),
    ])
    claims = atoms(run(report, repos=[str(repo)], agy=str(transcript)), 'K3')
    assert [c['reason_code'] for c in claims] == ['object_exists', 'object_exists', 'commit_exists']


def test_claims_object_type_direct_lookup_matches_batch(tmp_path):
    repo, _ = repository(tmp_path, {'logic.py': 'value = 1\n'})
    sha = git(repo, 'rev-parse', 'HEAD^{tree}')
    direct = Repositories([str(repo)], {}, {}, Budget(10)).commit(sha, object_kind='tree')
    batch = Repositories([str(repo)], {}, {}, Budget(10))
    batch.preload_commits([sha])
    assert direct == batch.commit(sha, object_kind='tree')


@pytest.mark.parametrize('source', ['value = 1\n', 'moved_helper()\n'])
def test_claims_other_file_definition_uses_fixed_commit(tmp_path, source):
    repo, revision = repository(tmp_path, {
        'logic.py': source, 'other.py': 'def moved_helper(): pass\n',
    })
    (repo/'other.py').write_text('def fabricated_helper(): pass\n', encoding='utf-8')
    git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qam', 'later synthetic state')
    report, _ = make_case(tmp_path, '`logic.py` 함수 `moved_helper` 정의가 있다.\n'
                         '`logic.py` 함수 `fabricated_helper` 정의가 있다.')
    claims = atoms(run(report, repos=[str(repo)], commits={str(repo): revision}), 'K4')
    assert claims[0]['reason_code'] == 'found_outside_scope'
    assert claims[0]['source']['repository_hits'][0]['revision'] == revision
    assert claims[0]['source']['repository_hits'][0]['path'] == 'other.py'
    assert (claims[1]['verdict'], claims[1]['reason_code']) == ('absent', 'not_found')


@pytest.mark.parametrize('other', [
    'moved_helper()\n', '# def moved_helper(): pass\n',
    'text = "def moved_helper(): pass"\n', 'def moved_helper_extra(): pass\n',
    'if moved_helper == candidate: pass\n',
])
def test_claims_missing_definition_not_rescued_by_mentions_or_comments(tmp_path, other):
    repo, revision = repository(tmp_path, {'logic.py': 'moved_helper()\n', 'other.py': other})
    report, _ = make_case(tmp_path, '`logic.py` 함수 `moved_helper` 정의가 있다.')
    claim = atoms(run(report, repos=[str(repo)], commits={str(repo): revision}), 'K4')[0]
    assert (claim['verdict'], claim['reason_code']) == ('absent', 'definition_missing')


def test_claims_definition_fallback_does_not_use_unrelated_repository(tmp_path):
    repo, revision = repository(tmp_path, {'logic.py': 'moved_helper()\n'})
    other_root = tmp_path/'unrelated'
    other_root.mkdir()
    other, other_revision = repository(other_root, {'other.py': 'def moved_helper(): pass\n'})
    report, _ = make_case(tmp_path, f'`{repo.as_posix()}/logic.py` 함수 `moved_helper` 정의가 있다.')
    claim = atoms(run(report, repos=[str(repo), str(other)],
                      commits={str(repo): revision, str(other): other_revision}), 'K4')[0]
    assert claim['reason_code'] == 'definition_missing'


def test_claims_definition_search_timeout_cannot_report_absence(tmp_path, monkeypatch):
    repo, revision = repository(tmp_path, {'logic.py': 'moved_helper()\n'})
    original = Repositories.git

    def timed_out(self, root, *args):
        if args[0] == 'grep':
            raise subprocess.TimeoutExpired('synthetic git grep', self.budget.remaining())
        return original(self, root, *args)

    monkeypatch.setattr(Repositories, 'git', timed_out)
    report, _ = make_case(tmp_path, '`logic.py` 함수 `moved_helper` 정의가 있다.')
    result = run(report, repos=[str(repo)], commits={str(repo): revision})
    assert result['timed_out']
    assert atoms(result, 'K4')[0]['reason_code'] == 'timed_out'
