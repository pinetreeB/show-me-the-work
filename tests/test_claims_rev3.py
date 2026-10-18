"""Precision contracts; all source content and repositories are synthetic."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from smtw.claims.check import run
from smtw.claims.extract import extract
from smtw.claims.model import Budget
from tests.claims_support import agy_call, generic, make_case, view
from tests.test_claims_contract import git_repo


MISSING = '1234567890abcdef1234567890abcdef12345678'


def atoms(result, kind):
    return [c for c in result['claims'] if c['check_type'] == kind]


def inspect(tmp_path, report, text='first source line\nrealToken\n'):
    path = 'C:/sample/widget.py'
    r, t = make_case(tmp_path, report, [agy_call('view_file', AbsolutePath=path), view(path, text)])
    return run(r, agy=str(t))


@pytest.mark.parametrize('token,verdict', [('realToken', 'match'), ('wrongToken', 'absent'), ('realToken()', 'match')])
@pytest.mark.parametrize('layout', ['`widget.py:1` `{token}`', '`widget.py:1` 설명 끝에 `{token}`',
                                  '| `widget.py` | 1행 `{token}` |', '`widget.py:1`\n`{token}`'])
def test_p6_single_tokens_are_only_k4(tmp_path, token, verdict, layout):
    result = inspect(tmp_path, layout.format(token=token))
    assert atoms(result, 'K1')[0]['verdict'] == 'range_only'
    assert atoms(result, 'K4')[0]['verdict'] == verdict


@pytest.mark.parametrize('snippet', ['1: first source line', 'result = call(value);', 'call(value)', 'print("sample");'])
def test_p6_numbered_and_adjacent_code_are_quotes(tmp_path, snippet):
    source = snippet.split(': ', 1)[1] if snippet.startswith('1:') else snippet
    result = inspect(tmp_path, '`widget.py:1` `'+snippet+'`', source)
    assert atoms(result, 'K1')[0]['verdict'] == 'match'
    result = inspect(tmp_path, '`widget.py:1` `'+snippet+'`', 'different source line')
    assert atoms(result, 'K1')[0]['verdict'] == 'mismatch'


@pytest.mark.parametrize('report,expected', [
    ('`widget.py:1` 설명 중간의 `result = call(value);` 뒤 문장', 'range_only'),
    ('`widget.py:1` 설명을 마친 뒤 `call(value)`', 'range_only'),
    ('`widget.py:1`\n설명 속 `result = call(value);`', 'range_only'),
    ('`widget.py:1` `value = true`', 'range_only'),
    # Fix2 N12 explicitly binds one blank line before a fenced quotation.
    ('`widget.py:1`\n\n```\nwrong source code\n```', 'mismatch')])
def test_p6_prose_gaps_weak_code_and_nonadjacent_fences_do_not_bind(tmp_path, report, expected):
    assert atoms(inspect(tmp_path, report), 'K1')[0]['verdict'] == expected


@pytest.mark.parametrize('report', ['`widget.py:1` “first source line”', '`widget.py:1`\n```\nfirst source line\n```',
    '`widget.py:1`\n1: first source line'])
def test_p6_explicit_quote_forms_remain_checked(tmp_path, report):
    assert atoms(inspect(tmp_path, report), 'K1')[0]['verdict'] == 'match'


@pytest.mark.parametrize('layout,expected', [
    ('커밋 `{sha}`', 'unverifiable'),
    ('저장소: `{root}` 커밋 `{sha}`', 'absent'),
    ('저장소: `{root}`\n커밋 `{sha}`', 'absent'),
    ('| `{root}` | `{sha}` |', 'absent'),
    ('저장소: `{root}`\n\n커밋 `{sha}`', 'unverifiable'),
    ('## 저장소: `{root}`\n커밋 `{sha}`', 'unverifiable'),
    ('| `{root}` | 설명 |\n| 다른 행 | `{sha}` |', 'unverifiable'),
    ('저장소: `{root}`\n저장소: omitted-project 커밋 `{sha}`', 'unverifiable'),
    ('저장소 repo 및 저장소 omitted-project 커밋 `{sha}`', 'unverifiable'),
])
def test_p7_absence_requires_explicit_repository_scope(tmp_path, git_repo, layout, expected):
    root, _ = git_repo
    r, _ = make_case(tmp_path, layout.format(root=root, sha=MISSING))
    claim = atoms(run(r, repos=[str(root)]), 'K3')[0]
    assert claim['verdict'] == expected
    assert claim['reason_code'] == ('commit_not_found' if expected == 'absent' else 'repo_scope_unknown')


@pytest.mark.parametrize('name', ['`repo`', '저장소 repo', 'repo 저장소'])
def test_p7_named_repository_can_establish_scope(tmp_path, git_repo, name):
    root, _ = git_repo
    r, _ = make_case(tmp_path, f'{name} 커밋 `{MISSING}`')
    assert atoms(run(r, repos=[str(root)]), 'K3')[0]['verdict'] == 'absent'


def test_p7_commit_option_alone_does_not_establish_repo_scope(tmp_path, git_repo):
    root, sha = git_repo
    r, _ = make_case(tmp_path, f'저장소: `{root}` 커밋 `{MISSING}`')
    claim = atoms(run(r, commits={str(root): sha}), 'K3')[0]
    assert (claim['verdict'], claim['reason_code']) == ('unverifiable', 'repo_scope_unknown')


def test_p7_other_repo_git_output_cannot_rescue_scoped_absence(tmp_path, git_repo):
    root, _ = git_repo
    r, t = make_case(tmp_path, f'저장소: `{root}` 커밋 `{MISSING}`', [
        agy_call('run_command', CommandLine='git log -1', Cwd='C:/other/project'), generic(MISSING)])
    assert atoms(run(r, repos=[str(root)], agy=str(t)), 'K3')[0]['verdict'] == 'absent'


def test_p7_existing_commit_still_matches_without_scope(tmp_path, git_repo):
    root, sha = git_repo
    r, _ = make_case(tmp_path, f'커밋 `{sha}`')
    assert atoms(run(r, repos=[str(root)]), 'K3')[0]['verdict'] == 'match'


@pytest.mark.parametrize('text,kind,verdict', [('1행', 'K1', 'range_only'), ('1~2행', 'K1', 'range_only'),
    ('1행 “first source line”', 'K1', 'match'), ('1: first source line', 'K5', 'match'),
    ('2: first source line', 'K5', 'mismatch')])
def test_p8_default_file_binds_only_unbound_positions(tmp_path, text, kind, verdict):
    source = tmp_path/'snapshot.py'
    source.write_text('first source line\nsecond source line', encoding='utf-8')
    r, _ = make_case(tmp_path, text)
    assert atoms(run(r, default_file={'widget.py': str(source)}), kind)[0]['verdict'] == verdict
    assert atoms(run(r, sources={'widget.py': str(source)}), kind)[0]['verdict'] == 'unverifiable'


@pytest.mark.parametrize('text', ['`other.py:1`', '`one.py` 및 `two.py` 1행'])
def test_p8_default_never_overrides_explicit_or_ambiguous_file(tmp_path, text):
    source = tmp_path/'snapshot.py'
    source.write_text('first source line', encoding='utf-8')
    r, _ = make_case(tmp_path, text)
    claim = atoms(run(r, default_file={'widget.py': str(source)}), 'K1')[0]
    assert claim['verdict'] == 'unverifiable'


def test_p8_default_does_not_bind_numbers_or_unlinked_identifiers(tmp_path):
    source = tmp_path/'snapshot.py'
    source.write_text('def valid_name(): pass', encoding='utf-8')
    r, _ = make_case(tmp_path, '함수 `valid_name`\n3줄')
    result = run(r, default_file={'widget.py': str(source)})
    assert atoms(result, 'K4')[0]['verdict'] == 'unverifiable'
    assert not atoms(result, 'K2')


@pytest.mark.parametrize('value,expected', [('widget.py={source}', 0), ('{source}', 2)])
def test_p8_cli_mapping_format(tmp_path, value, expected):
    source = tmp_path/'snapshot with spaces.py'
    source.write_text('first source line', encoding='utf-8')
    r, _ = make_case(tmp_path, '1: first source line')
    result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'smtw', 'claims', 'check', '--report', str(r),
                             '--default-file', value.format(source=source), '--format', 'json'], capture_output=True)
    assert result.returncode == expected
    assert json.loads(result.stdout)['status'] == ('PASS' if expected == 0 else 'ERROR')


def test_p8_conflicting_mapping_is_error(tmp_path):
    r, _ = make_case(tmp_path, '1행')
    with pytest.raises(ValueError, match='충돌'):
        run(r, sources={'widget.py': 'one.py'}, default_file={'widget.py': 'two.py'})


@pytest.mark.parametrize('body,claim,verdict,code', [
    ('File Path: C:/sample/widget.py\nTotal Lines: 1\n1: first', '2줄', 'mismatch', 'value_mismatch'),
    ('File Path: C:/sample/widget.py\nTotal Bytes: 4\n1: text', '9 B', 'mismatch', 'value_mismatch'),
    ('File Path: C:/sample/widget.py\nShowing lines 1 to 1\n1: first', '2줄', 'unverifiable', 'measurement_missing'),
    ('Error: cannot read', '2줄', 'unverifiable', 'read_failed'),
])
def test_p9_read_observation_precedes_source_file(tmp_path, monkeypatch, body, claim, verdict, code):
    def forbidden(*args, **kwargs):
        pytest.fail('existing transcript read must prevent local snapshot fallback')
    monkeypatch.setattr('smtw.claims.check.local_source', forbidden)
    r, t = make_case(tmp_path, '`widget.py` '+claim, [agy_call('view_file', AbsolutePath='C:/sample/widget.py'), generic(body)])
    item = atoms(run(r, agy=str(t), sources={'widget.py': str(tmp_path/'snapshot.py')}), 'K2')[0]
    assert (item['verdict'], item['reason_code']) == (verdict, code)


def test_p9_unknown_transcript_revision_never_falls_back_to_fixed_commit(tmp_path, git_repo):
    root, sha = git_repo
    r, t = make_case(tmp_path, f'commit {sha} `engine.py` 2줄',
        [agy_call('view_file', AbsolutePath=str(root/'engine.py')), view(str(root/'engine.py'), 'one')])
    item = atoms(run(r, agy=str(t), repos=[str(root)], commits={str(root): sha}), 'K2')[0]
    assert (item['verdict'], item['reason_code']) == ('unverifiable', 'observation_time_unresolved')


def test_p9_without_read_fixed_commit_remains_available(tmp_path, git_repo):
    root, sha = git_repo
    r, _ = make_case(tmp_path, '`engine.py` 2줄')
    assert atoms(run(r, commits={str(root): sha}), 'K2')[0]['verdict'] == 'match'


def test_p9_explicit_time_chooses_matching_read_not_later_snapshot(tmp_path):
    path = 'C:/sample/widget.py'
    r, t = make_case(tmp_path, '수정 전 `widget.py` 1줄', [agy_call('view_file', AbsolutePath=path), view(path, 'one'),
        agy_call('view_file', AbsolutePath=path), view(path, 'one\ntwo')])
    assert atoms(run(r, agy=str(t)), 'K2')[0]['verdict'] == 'match'


def test_verdict_reason_summary_counts_every_atom(tmp_path):
    result = inspect(tmp_path, '`widget.py:1`\n`widget.py` `wrongToken`\n`widget.py` 2줄\n커밋 `abcdef123`')
    assert result['verdict_reason_counts']['range_only'] == {'range_only': 1}
    assert result['verdict_reason_counts']['unverifiable']['repo_scope_unknown'] == 1
    for verdict, count in result['summary'].items():
        assert sum(result['verdict_reason_counts'][verdict].values()) == count
