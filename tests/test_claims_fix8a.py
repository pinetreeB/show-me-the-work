from __future__ import annotations

import subprocess

import pytest

from smtw.claims.check import run
from smtw.claims.sources import Repositories
from tests.claims_support import agy_call, make_case, view


def inspect(tmp_path, report, source='first\nsecond\nthird\n', name='notes.md', **kwargs):
    display = 'C:/synthetic/project/' + name
    report_path, transcript = make_case(
        tmp_path,
        report,
        [agy_call('view_file', AbsolutePath=display), view(display, source)],
    )
    return run(report_path, agy=str(transcript), **kwargs)


def atoms(result, kind):
    return [claim for claim in result['claims'] if claim['check_type'] == kind]


def git(repo, *args):
    return subprocess.run(
        ['git', '-C', str(repo), '-c', 'user.name=Fixture',
         '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false', *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def repository(tmp_path, files):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'fixture')
    return repo, git(repo, 'rev-parse', 'HEAD')


@pytest.mark.parametrize('report', [
    'Before calling `ghost_nowhere_fn()`, the notes.md loader is initialized.',
    'We call `ghost_nowhere_fn` after the loop in notes.md.',
    '수정 후 `notes.md`에서 함수 `ghost_nowhere_fn`을 호출한다.',
    'After the patch, `settings.yaml` has `missingKey: 2`.',
])
def test_general_time_words_do_not_skip_repository_absence(tmp_path, report):
    repo, revision = repository(tmp_path, {'module.py': 'def real_name():\n    return 1\n'})
    name = 'settings.yaml' if 'settings.yaml' in report else 'notes.md'
    source = 'realKey: 1\n' if name.endswith('.yaml') else 'plain text\n'
    result = inspect(tmp_path, report, source, name, repos=[str(repo)],
                     commits={str(repo): revision})
    relevant = [c for c in result['claims'] if c['check_type'] in {'K4', 'K6'}]
    assert relevant
    assert all(c['verdict'] == 'absent' for c in relevant)
    assert result['status'] == 'FAIL'


def test_conflicting_explicit_revision_is_skipped_with_reason(tmp_path):
    repo, revision = repository(tmp_path, {'module.py': 'def real_name():\n    return 1\n'})
    other = 'a' * 40
    result = inspect(
        tmp_path,
        f'커밋 {other} 에서 함수 `ghost_nowhere_fn`이 정의돼 있다.',
        repos=[str(repo)], commits={str(repo): revision},
    )
    claim = next(c for c in atoms(result, 'K4') if 'ghost_nowhere_fn' in c['text'])
    assert claim['verdict'] == 'unverifiable'
    assert claim['reason_code'] == 'repository_search_incomplete'
    assert '건너뜀' in str(claim['source'])


@pytest.mark.parametrize('report,reason_code', [
    ('`notes.md:2, 900` "second"', 'range_exceeded'),
    ('`notes.md:1, 2` `missing = condition()`', 'quote_mismatch'),
])
def test_ambiguous_reference_keeps_certain_failure(tmp_path, report, reason_code):
    result = inspect(tmp_path, report)
    if reason_code == 'range_exceeded':
        claims = atoms(result, 'K1')
        assert sum(c['verdict'] == 'mismatch' and c['reason_code'] == reason_code
                   for c in claims) == 1
    else:
        assert all(c['verdict'] == 'range_only' for c in atoms(result, 'K1'))
        claims = atoms(result, 'K5')
        assert len(claims) == 1
        assert claims[0]['verdict'] == 'mismatch' and claims[0]['reason_code'] == reason_code
    assert result['status'] == 'FAIL'


def test_ambiguous_reference_matches_quote_in_any_candidate_range(tmp_path):
    result = inspect(tmp_path, '`notes.md:1`, `notes.md:2`, `notes.md:3` `return "ok"`',
                     'first\nsecond\nreturn "ok"\n')
    assert all(c['verdict'] == 'range_only' for c in atoms(result, 'K1'))
    claims = atoms(result, 'K5')
    assert len(claims) == 1
    assert claims[0]['verdict'] == 'match' and claims[0]['reason_code'] == 'quote_match'
    assert result['status'] == 'PASS'


def test_ambiguous_quote_with_unobserved_candidate_stays_unknown(tmp_path):
    report, transcript = make_case(tmp_path, '`alpha.md:1` · `beta.md:1` `missing = value()`', [
        agy_call('view_file', AbsolutePath='C:/synthetic/project/alpha.md'),
        view('C:/synthetic/project/alpha.md', 'present = value()\n'),
    ])
    result = run(report, agy=str(transcript))
    range_claims = atoms(result, 'K1')
    assert any(c['verdict'] == 'range_only' for c in range_claims)
    assert all(c['verdict'] in {'range_only', 'unverifiable'} for c in range_claims)
    claims = atoms(result, 'K5')
    assert len(claims) == 1
    assert claims[0]['verdict'] == 'unverifiable'
    assert claims[0]['reason_code'] == 'binding_ambiguous'


def test_ambiguous_quote_with_partial_candidate_range_stays_unknown(tmp_path):
    report, transcript = make_case(tmp_path, '`alpha.md:1` · `beta.md:3` `missing = value()`', [
        agy_call('view_file', AbsolutePath='C:/synthetic/project/alpha.md'),
        view('C:/synthetic/project/alpha.md', 'present = value()\n'),
        agy_call('view_file', AbsolutePath='C:/synthetic/project/beta.md'),
        view('C:/synthetic/project/beta.md', 'one\ntwo\nthree\n', start=1, end=1),
    ])
    result = run(report, agy=str(transcript))
    quotes = atoms(result, 'K5')
    assert len(quotes) == 1
    assert quotes[0]['verdict'] == 'unverifiable'
    assert quotes[0]['reason_code'] == 'binding_ambiguous'


def test_ambiguous_quote_in_visible_part_of_partial_view_stays_unknown(tmp_path):
    report, transcript = make_case(tmp_path, '`alpha.md:1` · `beta.md:1` `missing = value()`', [
        agy_call('view_file', AbsolutePath='C:/synthetic/project/alpha.md'),
        view('C:/synthetic/project/alpha.md', 'present = value()\n'),
        agy_call('view_file', AbsolutePath='C:/synthetic/project/beta.md'),
        view('C:/synthetic/project/beta.md', 'also_present = value()\ntwo\nthree\n', start=1, end=1),
    ])
    result = run(report, agy=str(transcript))
    quotes = atoms(result, 'K5')
    assert len(quotes) == 1
    assert quotes[0]['verdict'] == 'unverifiable'
    assert quotes[0]['reason_code'] == 'binding_ambiguous'


def test_cross_file_direct_literal_is_one_group_quote_atom(tmp_path):
    rows = []
    for name, text in [('alpha.md', 'alpha = value()\n'), ('beta.md', 'beta = value()\n'),
                       ('gamma.md', 'gamma = value()\n')]:
        display = f'C:/synthetic/project/{name}'
        rows.extend([agy_call('view_file', AbsolutePath=display), view(display, text)])
    report, transcript = make_case(
        tmp_path, '`alpha.md:1` · `beta.md:1` · `gamma.md:1` `missing = value()`', rows)
    result = run(report, agy=str(transcript))
    assert len(atoms(result, 'K1')) == 3
    assert all(c['verdict'] == 'range_only' for c in atoms(result, 'K1'))
    quotes = atoms(result, 'K5')
    assert len(quotes) == 1
    assert quotes[0]['verdict'] == 'mismatch' and quotes[0]['reason_code'] == 'quote_mismatch'


def test_cross_file_direct_literal_matches_any_candidate(tmp_path):
    rows = []
    for name, text in [('alpha.md', 'alpha = value()\n'), ('beta.md', 'missing = value()\n'),
                       ('gamma.md', 'gamma = value()\n')]:
        display = f'C:/synthetic/project/{name}'
        rows.extend([agy_call('view_file', AbsolutePath=display), view(display, text)])
    report, transcript = make_case(
        tmp_path, '`alpha.md:1` · `beta.md:1` · `gamma.md:1` `missing = value()`', rows)
    result = run(report, agy=str(transcript))
    assert all(c['verdict'] == 'range_only' for c in atoms(result, 'K1'))
    quotes = atoms(result, 'K5')
    assert len(quotes) == 1
    assert quotes[0]['verdict'] == 'match' and quotes[0]['reason_code'] == 'quote_match'


@pytest.mark.parametrize('suffix,quote_count', [
    ('설명상 `missing = value()`', 1),
    ('`missing(..., value)`', 1),
    ('`first = value()` · `second = value()`', 2),
])
def test_open_candidate_quote_group_keeps_each_unknown_atom(tmp_path, suffix, quote_count):
    report = f'`alpha.md:1` · `beta.md:1` {suffix}'
    result = inspect(tmp_path, report, 'alpha = value()\nbeta = value()\n', 'alpha.md')
    assert all(c['verdict'] in {'range_only', 'unverifiable'} for c in atoms(result, 'K1'))
    quotes = atoms(result, 'K5')
    assert len(quotes) == quote_count
    assert all(c['verdict'] == 'unverifiable' for c in quotes)
    assert all(c['reason_code'] == 'binding_ambiguous' for c in quotes)


def test_reference_shorthand_is_not_a_literal_quote(tmp_path):
    result = inspect(tmp_path, '`notes.md:1, 2` `:165, :195`')
    assert len(atoms(result, 'K1')) == 2
    assert all(c['verdict'] == 'range_only' for c in atoms(result, 'K1'))
    assert not atoms(result, 'K5')


@pytest.mark.parametrize('report', [
    '이전 보고서에 따르면 `notes.md`는 총 99줄이다.',
    '`notes.md`는 총 99줄이라고 적었다.',
    '이전 보고가 `notes.md:900`을 언급했다.',
])
def test_own_or_uncontrasted_reporting_does_not_hide_mismatch(tmp_path, report):
    result = inspect(tmp_path, report)
    relevant = [c for c in result['claims'] if c['check_type'] in {'K1', 'K2'}]
    assert any(c['verdict'] == 'mismatch' for c in relevant)
    assert all(c['reason_code'] != 'reported_claim' for c in relevant)
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('report', [
    'The old report claimed notes.md has 53 lines, but actually it has 99 lines.',
    '이전 보고는 `notes.md`가 53줄이라고 주장했지만 실제 99줄이다.',
])
def test_third_party_report_with_contrast_keeps_old_value_reported(tmp_path, report):
    result = inspect(tmp_path, report)
    claims = atoms(result, 'K2')
    assert any(c['reason_code'] == 'reported_claim' and '53' in c['text'] for c in claims)
    assert any(c['verdict'] == 'mismatch' and '99' in c['text'] for c in claims)
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('report', [
    'I reported that notes.md has 99 lines, but actually it has 3 lines.',
    'We reported that notes.md has 99 lines, but actually it has 3 lines.',
    'This report claimed notes.md has 99 lines, but actually it has 3 lines.',
    '`notes.md` claimed 99 lines, but actually it has 3 lines.',
    'Our report claimed notes.md has 99 lines, but actually it has 3 lines.',
    'Current report claimed notes.md has 99 lines, but actually it has 3 lines.',
    'My report claimed notes.md has 99 lines, but actually it has 3 lines.',
    'The report claimed notes.md has 99 lines, but actually it has 3 lines.',
    '우리는 `notes.md`가 총 99줄이라고 보고했지만 실제 총 3줄이다.',
    '본 보고서는 `notes.md`가 총 99줄이라고 보고했지만 실제 총 3줄이다.',
    '이 보고서는 `notes.md`가 총 99줄이라고 적었지만 실제 총 3줄이다.',
    '현재 보고서는 `notes.md`가 총 99줄이라고 적었지만 실제 총 3줄이다.',
])
def test_first_person_report_with_contrast_is_not_third_party(tmp_path, report):
    result = inspect(tmp_path, report)
    claims = atoms(result, 'K2')
    assert claims
    assert all(c['reason_code'] != 'reported_claim' for c in claims)
    assert any(c['verdict'] == 'mismatch' and '99' in c['text'] for c in claims)


@pytest.mark.parametrize('claim,kind', [
    ('총 99줄', 'K2'), ('함수 `real_name`은 L1-L900에 있다.', 'K1'), ('크기는 9999 bytes이다.', 'K2'),
])
def test_single_file_heading_does_not_relax_wrong_inherited_metric(tmp_path, claim, kind):
    result = inspect(tmp_path, '### `notes.md`\n' + claim)
    target = atoms(result, kind)[0]
    assert target['verdict'] == 'mismatch'
    assert target['reason_code'] != 'inherited_binding'
    assert result['status'] == 'FAIL'


def test_other_path_in_same_paragraph_retains_inherited_uncertainty(tmp_path):
    report, transcript = make_case(tmp_path, '### `notes.md`\n`other.md`는 별도다. 이 파일은 총 99줄이다.', [
        agy_call('view_file', AbsolutePath='C:/synthetic/project/notes.md'),
        view('C:/synthetic/project/notes.md', 'first\nsecond\nthird\n'),
    ])
    result = run(report, agy=str(transcript))
    claim = atoms(result, 'K2')[0]
    assert claim['verdict'] == 'unverifiable'
    assert claim['reason_code'] in {'binding_ambiguous', 'inherited_binding'}


def test_other_path_on_previous_line_in_section_retains_inherited_uncertainty(tmp_path):
    report, transcript = make_case(tmp_path, '### `notes.md`\n`other.md`는 별도다.\n이 파일은 총 99줄이다.', [
        agy_call('view_file', AbsolutePath='C:/synthetic/project/other.md'),
        view('C:/synthetic/project/other.md', 'first\nsecond\nthird\n'),
    ])
    result = run(report, agy=str(transcript))
    claim = atoms(result, 'K2')[0]
    assert claim['verdict'] == 'unverifiable'
    assert claim['reason_code'] == 'inherited_binding'


def test_repository_identifier_records_definition_location_at_commit(tmp_path):
    repo, revision = repository(tmp_path, {'module.py': 'def real_name():\n    return 1\n'})
    result = inspect(tmp_path, '`notes.md` 함수 `real_name`이 정의돼 있다.',
                     repos=[str(repo)], commits={str(repo): revision})
    claim = atoms(result, 'K4')[0]
    assert claim['reason_code'] == 'found_outside_scope'
    assert claim['source']['repository_hits'] == [{
        'path': 'module.py', 'line': 1, 'revision': revision, 'kind': 'definition',
    }]


@pytest.mark.parametrize('files', [
    {'notes.md': 'ghost_comment_fn is mentioned here\n'},
    {'module.py': '# def ghost_comment_fn(): pass\nVALUE = 1\n'},
    {'module.py': 'VALUE = 1  # ghost_comment_fn\n'},
    {'module.js': 'const value = 1; // ghost_comment_fn\n'},
    {'module.py': '\"\"\"ghost_comment_fn is documentation only.\"\"\"\nVALUE = 1\n'},
    {'module.py': '\"\"\"Documentation.\nghost_comment_fn is not code.\n\"\"\"\nVALUE = 1\n'},
    {'module.py': '\"\"\"Documentation.\nghost_comment_fn() is illustrative.\n\"\"\"\nVALUE = 1\n'},
    {'module.py': '\"\"\"Documentation.\ndef ghost_comment_fn(): pass\n\"\"\"\nVALUE = 1\n'},
    {'module.py': 'message = \"ghost_comment_fn()\"\nVALUE = 1\n'},
])
def test_document_or_comment_only_identifier_hit_remains_absent(tmp_path, files):
    repo, revision = repository(tmp_path, files)
    result = inspect(tmp_path, '`notes.md` 함수 `ghost_comment_fn`이 정의돼 있다.',
                     repos=[str(repo)], commits={str(repo): revision})
    claim = atoms(result, 'K4')[0]
    assert claim['verdict'] == 'absent'
    assert claim['reason_code'] == 'not_found'
    assert result['status'] == 'FAIL'


def test_repository_assignment_records_definition_location(tmp_path):
    repo, revision = repository(tmp_path, {'module.py': 'assigned_name = 1\n'})
    result = inspect(tmp_path, '`notes.md` 변수 `assigned_name`이 정의돼 있다.',
                     repos=[str(repo)], commits={str(repo): revision})
    claim = atoms(result, 'K4')[0]
    assert claim['reason_code'] == 'found_outside_scope'
    assert claim['source']['repository_hit_kind'] == 'definition'
    assert claim['source']['repository_hits'][0]['line'] == 1


@pytest.mark.parametrize('name,content,key', [
    ('module.js', 'const config = {"jsKey": 2};\n', 'jsKey'),
    ('module.py', 'CONFIG = {"dictKey": 2}\n', 'dictKey'),
])
def test_repository_quoted_mapping_key_is_a_definition(tmp_path, name, content, key):
    repo, revision = repository(tmp_path, {name: content})
    result = inspect(tmp_path, f'`notes.md` 설정 키 `{key}: 2`가 있다.',
                     repos=[str(repo)], commits={str(repo): revision})
    claims = [c for c in result['claims'] if c['check_type'] in {'K4', 'K6'}]
    assert claims
    assert all(c['reason_code'] == 'found_outside_scope' for c in claims)
    assert all(c['source']['repository_hit_kind'] == 'definition' for c in claims)
    assert all(c['source']['repository_hits'][0]['revision'] == revision for c in claims)


def test_repository_docstring_mapping_key_remains_absent(tmp_path):
    repo, revision = repository(tmp_path, {
        'module.py': '\"\"\"Example only.\n{\"ghostKey\": 2}\n\"\"\"\nVALUE = 1\n',
    })
    result = inspect(tmp_path, '`notes.md` 설정 키 `ghostKey: 2`가 있다.',
                     repos=[str(repo)], commits={str(repo): revision})
    claims = [c for c in result['claims'] if c['check_type'] in {'K4', 'K6'}]
    assert claims
    assert all(c['verdict'] == 'absent' and c['reason_code'] == 'not_found' for c in claims)


def test_overlapping_repository_identifiers_keep_independent_hits(tmp_path):
    repo, revision = repository(tmp_path, {'module.py': 'foo = 1\nfoo.bar = 2\n'})
    result = inspect(tmp_path, '`notes.md` 변수 `foo`와 `foo.bar`가 정의돼 있다.',
                     repos=[str(repo)], commits={str(repo): revision})
    claims = [c for c in atoms(result, 'K4') if c['text'] in {'`foo`', '`foo.bar`'}]
    assert len(claims) == 2
    assert all(c['reason_code'] == 'found_outside_scope' for c in claims)


def test_short_general_quote_next_to_reference_is_not_a_quote_atom(tmp_path):
    result = inspect(tmp_path, '`notes.md:1`의 탭 이름은 "raw"이다.')
    assert not atoms(result, 'K5')


def test_short_backtick_creates_one_self_assigned_item_atom(tmp_path):
    result = inspect(tmp_path, '`notes.md` R4-N1 `raw`')
    claims = atoms(result, 'K9')
    assert len(claims) == 1
    assert claims[0]['reason_code'] == 'self_assigned_item_id'


def test_short_backtick_code_is_not_a_line_quote_without_explicit_order(tmp_path):
    result = inspect(tmp_path, '`notes.md:1` `x = 2`', 'x = 1\n')
    assert atoms(result, 'K1')[0]['verdict'] == 'range_only'


def test_short_quotes_are_checked_only_with_explicit_order(tmp_path):
    result = inspect(tmp_path, '`notes.md:1, 2`에 각각 "first"·"second"')
    assert [c['verdict'] for c in atoms(result, 'K1')] == ['match', 'match']


def test_single_reference_each_marker_does_not_enable_short_quote(tmp_path):
    result = inspect(tmp_path, '`notes.md:1`에 각각 "raw"', 'raw\n')
    assert atoms(result, 'K1')[0]['verdict'] == 'range_only'
    assert not atoms(result, 'K5')


def test_direct_read_token_match_remains_supported(tmp_path):
    result = inspect(tmp_path, '`notes.py` 함수 `real_name`이 정의돼 있다.',
                     'def real_name():\n    return 1\n', 'notes.py')
    claim = atoms(result, 'K4')[0]
    assert claim['verdict'] == 'match'
    assert claim['reason_code'] == 'token_match'


def test_repository_identifiers_are_grepped_once_per_repo_and_commit(tmp_path, monkeypatch):
    repo, revision = repository(tmp_path, {
        'module.py': 'def real_alpha(): pass\ndef real_beta(): pass\ndef real_gamma(): pass\n',
    })
    calls = []
    original = Repositories.git

    def recording_git(self, root, *args):
        if args and args[0] == 'grep':
            calls.append(args)
        return original(self, root, *args)

    monkeypatch.setattr(Repositories, 'git', recording_git)
    result = inspect(
        tmp_path,
        '`notes.md` 함수 `real_alpha`, `real_beta`, `real_gamma`, `ghost_alpha`, `ghost_beta`가 정의돼 있다.',
        repos=[str(repo)], commits={str(repo): revision},
    )
    assert result['status'] == 'FAIL'
    assert len(calls) == 1
    assert calls[0].count('-e') == 5
    assert '-F' in calls[0] and '-w' in calls[0]
