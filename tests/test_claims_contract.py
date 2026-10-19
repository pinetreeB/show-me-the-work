from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest

from smtw.claims.check import Checker, check_one, run
from smtw.claims.extract import extract
from smtw.claims.model import Budget, Claim, Evidence, line_counts, summarize
from smtw.claims.paths import mapping, matches_path, normalize_url
from smtw.claims.sources import local_source
from smtw.claims.transcripts import read_transcript, resolve_agy
from tests.claims_support import agy_call, generic, make_case, view, write_transcript


def check(tmp_path, report, source, path='C:/sample/engine.py', **kwargs):
    r, t = make_case(tmp_path, report, [agy_call('view_file', AbsolutePath=path), view(path, source)])
    return run(r, agy=str(t), **kwargs)


def only(result, kind):
    return [c for c in result['claims'] if c['check_type'] == kind]


@pytest.mark.parametrize('text,expected', [('', (0, 0, 0)), ('a', (0, 1, 1)),
                                         ('a\n', (1, 1, 2)), ('a\r\nb\r\n', (2, 2, 3)),
                                         ('a\r\nb', (1, 2, 2)), ('\n', (1, 1, 2))])
def test_required_16_three_line_counts(text, expected):
    assert tuple(line_counts(text).values()) == expected


@pytest.mark.parametrize('reference,quote,verdict', [
    ('engine.py:1', 'return valid_value', 'match'),
    ('engine.py:L1', 'return valid_value', 'match'),
    ('engine.py:1-2', 'return valid_value', 'match'),
    ('engine.py:1', 'return wrong_value', 'mismatch'),
    ('engine.py:8', '', 'mismatch'),
])
def test_k1_reference_atoms(tmp_path, reference, quote, verdict):
    result = check(tmp_path, f'`{reference}` “{quote}”', 'return valid_value\n')
    assert only(result, 'K1')[0]['verdict'] == verdict
    assert reference in only(result, 'K1')[0]['text']


def test_k1_code_fence_and_fictional_code(tmp_path):
    result = check(tmp_path, '`engine.py:1`\n```py\nreturn wrong_value\n```\n예시\n```\nfiction.py:999\n```', 'return valid_value\n')
    assert len(only(result, 'K1')) == 1
    assert only(result, 'K1')[0]['verdict'] == 'mismatch'


@pytest.mark.parametrize('source,number,verdict', [
    ('x\n', 1, 'match'), ('x\n', 2, 'match'), ('x', 0, 'mismatch'),
    ('', 0, 'match'), ('x\r\n', 2, 'match'), ('x\n', 3, 'mismatch')])
def test_required_08_k2_generic_lines(tmp_path, source, number, verdict):
    result = check(tmp_path, f'`engine.py` ({number}줄)', source)
    assert only(result, 'K2')[0]['verdict'] == verdict


@pytest.mark.parametrize('metric,number,verdict', [('wc -l', 1, 'match'), ('wc -l', 2, 'mismatch'),
                                                ('Total Lines', 2, 'match'), ('Total Lines', 1, 'mismatch')])
def test_k2_explicit_count_conventions(tmp_path, metric, number, verdict):
    result = check(tmp_path, f'`engine.py` {metric} {number}줄', 'x\n')
    assert only(result, 'K2')[0]['verdict'] == verdict


@pytest.mark.parametrize('number,unit,verdict', [('2', 'B', 'match'), ('0.002', 'KB', 'match'),
                                              ('3', 'B', 'mismatch'), ('1', 'MB', 'mismatch')])
def test_k2_bytes_are_file_bytes(tmp_path, number, unit, verdict):
    result = check(tmp_path, f'`engine.py` {number} {unit}', 'x\n')
    assert only(result, 'K2')[0]['verdict'] == verdict


@pytest.fixture
def git_repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    (root / 'engine.py').write_text('def valid_name():\n    return 1\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(root), 'add', 'engine.py'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Synthetic Tester', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', 'synthetic source'], check=True)
    sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    return root, sha


@pytest.mark.parametrize('variant,expected', [('full', 'match'), ('short', 'match'), ('missing', 'unverifiable'), ('blob', 'mismatch')])
def test_k3_commit_object_type(tmp_path, git_repo, variant, expected):
    root, sha = git_repo
    if variant == 'short':
        sha = sha[:8]
    elif variant == 'missing':
        sha = '1234567890abcdef1234567890abcdef12345678'
    elif variant == 'blob':
        sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD:engine.py'], text=True).strip()
    report, _ = make_case(tmp_path, f'커밋 `{sha}`')
    assert only(run(report, repos=[str(root)]), 'K3')[0]['verdict'] == expected


@pytest.mark.parametrize('claim,source,verdict', [
    ('함수 `valid_name`', 'def valid_name():\n    pass\n', 'match'),
    ('설정 키 `enabledFlag`', '{"enabledFlag": true}\n', 'match'),
    ('함수 `valid_name`', '# valid_name mentioned\n', 'absent'),
    ('함수 `valid_name`', 'def valid_name_extra():\n    pass\n', 'absent'),
])
def test_k4_definition_and_token_boundary(tmp_path, claim, source, verdict):
    result = check(tmp_path, f'`engine.py` {claim}', source)
    assert only(result, 'K4')[0]['verdict'] == verdict


@pytest.mark.parametrize('token', ['--flag', '[사실]', '<PLACEHOLDER>', 'normal', 'two words'])
def test_required_10_plain_backticks_are_not_identifiers(token):
    claims, _ = extract(f'일반 설명 `{token}`', Budget(10))
    assert not [c for c in claims if c.check_type == 'K4']


@pytest.mark.parametrize('quote,verdict', [('abcdefgh value', 'match'), ('abcdefgh   value', 'match'),
                                        ('abcdefgh false', 'mismatch'), ('missing evidence', 'mismatch')])
def test_k5_literal_quotes(tmp_path, quote, verdict):
    result = check(tmp_path, f'`engine.py` “{quote}”', 'abcdefgh value\n')
    assert only(result, 'K5')[0]['verdict'] == verdict


def test_translation_not_k5():
    claims, _ = extract('`guide.md` 번역: “abcdefgh value”', Budget(10))
    assert not [c for c in claims if c.check_type == 'K5']


@pytest.mark.parametrize('value,verdict', [('fast-mode', 'match'), ('slow-mode', 'mismatch'),
                                        ('0', 'mismatch'), ('fast-mode ', 'match')])
def test_k6_key_value_is_not_quote(tmp_path, value, verdict):
    result = check(tmp_path, f'`engine.py` 설정 `runMode: "{value}"`', '{"runMode": "fast-mode"}\n')
    assert not only(result, 'K5')
    assert only(result, 'K6')[0]['verdict'] == verdict


@pytest.mark.parametrize('value,verdict', [('12', 'match'), ('8', 'mismatch'), ('99', 'mismatch')])
def test_k6_entity_does_not_match_unrelated_number(tmp_path, value, verdict):
    result = check(tmp_path, f'`engine.py` 변수 `firstCount` {value}개', 'firstCount: 12개\notherCount: 99개\n')
    assert only(result, 'K6')[0]['verdict'] == verdict


@pytest.mark.parametrize('total,part,verdict', [(3, 2, 'match'), (3, 3, 'match'), (2, 3, 'mismatch'), (0, 1, 'mismatch')])
def test_k7_internal_arithmetic(tmp_path, total, part, verdict):
    report, _ = make_case(tmp_path, f'{total}건 중 {part}건')
    assert only(run(report), 'K7')[0]['verdict'] == verdict


@pytest.mark.parametrize('total,verdict', [(2, 'match'), (3, 'mismatch')])
def test_k7_table_count(tmp_path, total, verdict):
    report, _ = make_case(tmp_path, f'| ID | 판정 |\n|---|---|\n| A | PASS |\n| B | FAIL |\n\n합계 {total}건')
    assert only(run(report), 'K7')[0]['verdict'] == verdict


@pytest.mark.parametrize('mode,verdict', [('read', 'match'), ('normalized', 'match'), ('query', 'absent'), ('failed', 'unverifiable')])
def test_k8_successful_url_read(tmp_path, mode, verdict):
    url = 'https://example.invalid/doc'
    name = 'search_web' if mode == 'query' else 'read_url_content'
    args = {'Query': url} if mode == 'query' else {'Url': url}
    r, t = make_case(tmp_path, url if mode != 'normalized' else 'https://EXAMPLE.invalid:443/doc#part',
                     [agy_call(name, **args), generic('source text here', 'ERROR' if mode == 'failed' else 'DONE')])
    assert only(run(r, agy=str(t)), 'K8')[0]['verdict'] == verdict


@pytest.mark.parametrize('item,quote,verdict', [('R1', 'first function', 'match'), ('R2', 'second function', 'match'),
                                           ('R1', 'second function', 'mismatch'), ('R3', 'first function', 'absent')])
def test_k9_item_binding(tmp_path, item, quote, verdict):
    result = check(tmp_path, f'`guide.md` {item}: “{quote}”',
                   '# R1\nfirst function\n# R2\nsecond function\n', path='C:/sample/guide.md')
    assert only(result, 'K9')[0]['verdict'] == verdict


@pytest.mark.parametrize('text,warn', [('제가 업체에 전화해 확인했습니다.', True), ('출처: 유선 상담 기준', True),
                                    ('제가 전화하지 않았습니다.', False), ('예시: 제가 업체에 전화해 확인했습니다.', False),
                                    ('> 담당자가 유선 상담으로 확인했습니다.', False),
                                    ('담당자가 방문해 확인했습니다.', False)])
def test_required_13_k10_advisory_only(text, warn):
    claims, warnings = extract(text, Budget(10))
    assert bool(warnings) == warn
    assert not [c for c in claims if c.check_type == 'K10']


def test_required_01_changed_file_not_last_read(tmp_path):
    path = 'C:/sample/engine.py'
    rows = [agy_call('view_file', AbsolutePath=path), view(path, 'before_value\n'),
            agy_call('view_file', AbsolutePath=path), view(path, 'after_value\n')]
    r, t = make_case(tmp_path, '`engine.py:1` “before_value”', rows)
    assert only(run(r, agy=str(t)), 'K1')[0]['verdict'] == 'unverifiable'
    r.write_text('수정 전 `engine.py:1` “before_value”', encoding='utf-8')
    assert only(run(r, agy=str(t)), 'K1')[0]['verdict'] == 'match'


def test_required_02_self_citation_and_generated_echo(tmp_path):
    text = '`engine.py` 함수 `invented_name`'
    r, t = make_case(tmp_path, text)
    write_transcript(t, [agy_call('write_to_file', TargetFile=str(r), CodeContent=text), generic('saved'),
                         agy_call('view_file', AbsolutePath=str(r)), view(str(r), text),
                         agy_call('write_to_file', TargetFile='C:/sample/engine.py', CodeContent='def invented_name(): pass'), generic('saved'),
                         agy_call('view_file', AbsolutePath='C:/sample/engine.py'), view('C:/sample/engine.py', 'def invented_name(): pass'),
                         agy_call('run_command', CommandLine='echo "def invented_name(): pass"'), generic('def invented_name(): pass')])
    assert only(run(r, agy=str(t)), 'K4')[0]['verdict'] != 'match'


def test_pre_write_read_remains_evidence(tmp_path):
    path = 'C:/sample/engine.py'
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`', [
        agy_call('view_file', AbsolutePath=path), view(path, 'def valid_name(): pass'),
        agy_call('write_to_file', TargetFile=path, CodeContent='nothing'), generic('saved'),
        agy_call('view_file', AbsolutePath=path), view(path, 'nothing')])
    assert only(run(r, agy=str(t)), 'K4')[0]['verdict'] == 'match'


def test_required_03_current_head_is_only_annotation(tmp_path, git_repo):
    root, _ = git_repo
    report, _ = make_case(tmp_path, '`engine.py:1` “def valid_name():”')
    claim = only(run(report, repos=[str(root)]), 'K1')[0]
    assert claim['verdict'] == 'unverifiable'
    assert claim['source']['current_observations'][0]['tool'] == '현재 상태 관측'


def test_required_04_batched_calls_skip_system_and_crosscheck_paths(tmp_path):
    batch = agy_call('view_file', AbsolutePath='C:/sample/a.py')
    batch['tool_calls'].append({'name': 'view_file', 'args': {'AbsolutePath': 'C:/sample/b.py'}})
    r, t = make_case(tmp_path, '`a.py` (1줄)\n`b.py` (2줄)', [batch,
        {'type': 'EPHEMERAL_MESSAGE', 'content': 'do not use this'}, view('C:/sample/a.py', 'x'),
        {'type': 'SYSTEM_MESSAGE', 'content': 'side notice'}, view('C:/sample/b.py', 'x\ny')])
    assert all(c['verdict'] == 'match' for c in only(run(r, agy=str(t)), 'K2'))
    rows = [batch, view('C:/sample/b.py', 'x\ny'), view('C:/sample/a.py', 'x')]
    write_transcript(t, rows)
    assert all(c['verdict'] == 'unverifiable' for c in only(run(r, agy=str(t)), 'K2'))


@pytest.mark.parametrize('text,source,status,marked,exempt', [
    ('[미확인] `engine.py` 함수 `missing_name`', 'pass', 'INCOMPLETE', True, True),
    ('[추정] `engine.py` 함수 `valid_name`', 'def valid_name(): pass', 'PASS', True, True),
    ('[미확인] `engine.py:1` “wrong quoted content”', 'right source content', 'FAIL', True, False),
    ('[미확인] `engine.py` “wrong quoted content”', 'right source content', 'FAIL', True, False),
    ('[사실] `engine.py` 함수 `missing_name`', 'pass', 'FAIL', False, False),
])
def test_required_05_11_marks_do_not_change_verdict(tmp_path, text, source, status, marked, exempt):
    result = check(tmp_path, text, source)
    assert result['status'] == status
    assert result['claims'][0]['author_marked'] == marked
    assert result['claims'][0]['exempt'] == exempt


def test_required_11_marked_without_evidence_is_incomplete(tmp_path):
    report, _ = make_case(tmp_path, '[미확인] `engine.py` 함수 `missing_name`')
    result = run(report)
    assert result['status'] == 'INCOMPLETE'
    assert result['author_marked_review']


def test_mark_scope_sentence_cell_and_quote():
    claims, _ = extract('[추정] `a.py` 함수 `first_name`. `b.py` 함수 `second_name`\n'
                        '| [미확인] `c.py` 함수 `third_name` | `d.py` 함수 `fourth_name` |\n'
                        '`e.py` “이 인용의 [미확인] 문자열” 함수 `fifth_name`', Budget(10))
    assert [c.author_marked for c in claims if c.check_type == 'K4'] == [True, False, True, False, False]


def test_required_06_07_aux_pointer_lazy_and_task_completion(tmp_path):
    aux = tmp_path / 'aux'
    (aux / 'steps/5').mkdir(parents=True)
    (aux / 'tasks').mkdir()
    (aux / 'steps/5/content.md').write_text('Source: https://example.invalid/doc\nsource paragraph here', encoding='utf-8')
    (aux / 'tasks/task-8.log').write_text('a'*1024 + '\n', encoding='utf-8')
    # An unrelated large log must never be opened.
    (aux / 'tasks/task-99.log').write_bytes(b'x'*35000000)
    rows = [agy_call('read_url_content', Url='https://example.invalid/doc'),
            generic('has been saved to: C:/fake-brain/.system_generated/steps/5/content.md'),
            agy_call('run_command', CommandLine='git log -1'),
            generic('background task task-8\nTask logs are available at: C:/fake-brain/.system_generated/tasks/task-8.log'),
            agy_call('view_file', AbsolutePath='C:/fake-brain/.system_generated/steps/5/content.md'),
            view('C:/fake-brain/.system_generated/steps/5/content.md', 'source paragraph here'),
            {'type': 'SYSTEM_MESSAGE', 'content': 'Task id 8 finished with result: command exited with code 0'}]
    r, t = make_case(tmp_path, 'https://example.invalid/doc', rows)
    start = time.monotonic()
    result = run(r, agy=str(t))
    assert result['status'] == 'PASS'
    assert time.monotonic() - start < 10
    transcript = read_transcript(t, 'agy', r, r.read_text(), {}, Budget(10))
    assert transcript.evidence[1].complete
    assert transcript.evidence[0].url_content_path.endswith('content.md')
    assert not transcript.evidence[0].complete
    assert transcript.evidence[0].aux_path == ''
    write_transcript(t, rows[:-1])
    transcript = read_transcript(t, 'agy', r, r.read_text(), {}, Budget(10))
    assert transcript.evidence[1].problem == '비동기 완료 정보 미확보'


def test_required_09_cp949_and_damaged_line_scope(tmp_path):
    source = tmp_path / 'legacy.txt'
    source.write_bytes('정확한 한국어 원문입니다\n'.encode('cp949'))
    r, _ = make_case(tmp_path, '`legacy.txt` “정확한 한국어 원문입니다”')
    assert run(r, sources={'legacy.txt': str(source)})['status'] == 'PASS'
    source.write_text('정확한 한국어 원문입니다\n손상 \ufffd\n', encoding='utf-8')
    assert run(r, sources={'legacy.txt': str(source)})['status'] == 'PASS'
    r.write_text('`legacy.txt` “찾을 수 없는 다른 원문”', encoding='utf-8')
    assert run(r, sources={'legacy.txt': str(source)})['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('after_error,expected', [(True, 'FAIL'), (False, 'INCOMPLETE')])
def test_required_12_timeout_preserves_found_failure(tmp_path, monkeypatch, after_error, expected):
    r, t = make_case(tmp_path, '`engine.py` (9줄)\n`engine.py` (10줄)',
                     [agy_call('view_file', AbsolutePath='C:/sample/engine.py'), view('C:/sample/engine.py', 'x')])
    budget = Budget(60)
    apply = Checker.apply
    def expire(self, c):
        if after_error:
            apply(self, c)
            budget.end = 0
        else:
            budget.end = 0
            budget.check()
    monkeypatch.setattr(Checker, 'apply', expire)
    result = run(r, agy=str(t), budget=budget)
    assert result['status'] == expected
    assert result['timed_out']
    assert result['unchecked_count'] == (1 if after_error else 2)


def test_required_14_fixed_commit_conflicting_explicit_time(tmp_path, git_repo):
    root, sha = git_repo
    r, _ = make_case(tmp_path, '`engine.py:1`')
    assert only(run(r, repos=[str(root)], commits={str(root): sha}), 'K1')[0]['verdict'] == 'range_only'
    r.write_text('commit abcdef1234 `engine.py:1`', encoding='utf-8')
    assert only(run(r, repos=[str(root)], commits={str(root): sha}), 'K1')[0]['verdict'] == 'unverifiable'


def test_required_15_docx_unsupported_regions(tmp_path):
    doc = tmp_path / 'guide.docx'
    with zipfile.ZipFile(doc, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>supported paragraph</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>supported table value</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        archive.writestr('word/header1.xml', '<header>unsupported quotation</header>')
    r, _ = make_case(tmp_path, '`guide.docx` “unsupported quotation”')
    assert run(r, sources={'guide.docx': str(doc)})['status'] == 'INCOMPLETE'
    r.write_text('`guide.docx` “supported table value”', encoding='utf-8')
    assert run(r, sources={'guide.docx': str(doc)})['status'] == 'PASS'


def test_basename_ambiguity_and_alias(tmp_path):
    rows = [agy_call('view_file', AbsolutePath='C:/one/engine.py'), view('C:/one/engine.py', 'x'),
            agy_call('view_file', AbsolutePath='C:/two/engine.py'), view('C:/two/engine.py', 'x\ny')]
    r, t = make_case(tmp_path, '`engine.py` (1줄)', rows)
    assert run(r, agy=str(t))['status'] == 'INCOMPLETE'
    r.write_text('`<SOURCE>/engine.py` (1줄)', encoding='utf-8')
    assert run(r, agy=str(t), aliases={'<SOURCE>': 'C:/one'})['status'] == 'PASS'


def test_source_mapping_snapshot_name(tmp_path):
    source = tmp_path / 'different.py@abc12345'
    source.write_text('x', encoding='utf-8')
    r, _ = make_case(tmp_path, '`engine.py` (1줄)')
    assert run(r, sources=mapping([f'engine.py={source}'], basename_default=True))['status'] == 'PASS'


def test_last_report_write_window_current_only(tmp_path):
    report_text = '`engine.py` (1줄)'
    r, t = make_case(tmp_path, report_text)
    path = 'C:/sample/engine.py'
    write_transcript(t, [agy_call('view_file', AbsolutePath=path), view(path, 'x'),
        agy_call('write_to_file', TargetFile='C:/sample/result.md', CodeContent=report_text), generic('saved'),
        agy_call('view_file', AbsolutePath=path), view(path, 'x\ny')])
    assert run(r, agy=str(t))['status'] == 'PASS'
    result = read_transcript(t, 'agy', r, report_text, {}, Budget(10))
    assert [e.after_report for e in result.evidence] == [False, True]


def test_optional_command_line_number_partial(tmp_path):
    r, t = make_case(tmp_path, '`engine.py:20` “return valid_value”\n`engine.py` 함수 `missing_name`', [
        agy_call('run_command', CommandLine='Get-Content C:/sample/engine.py | Select-Object -Skip 19 -First 1'),
        generic('line 20: return valid_value')])
    result = run(r, agy=str(t))
    assert only(result, 'K1')[0]['verdict'] == 'match'
    assert only(result, 'K4')[0]['verdict'] == 'unverifiable'


def test_aux_default_brain_layout_and_id(tmp_path):
    path = resolve_agy('fake-conversation', tmp_path)
    assert path == tmp_path / 'fake-conversation/.system_generated/logs/transcript_full.jsonl'
    assert resolve_agy(str(tmp_path / 'transcript_slice.jsonl'), tmp_path) == tmp_path / 'transcript_slice.jsonl'


def test_schema_fields_and_atomic_claims(tmp_path):
    result = check(tmp_path, '`engine.py:1` 함수 `first_name`, `second_name`, 8줄', 'def first_name(): pass')
    assert len(result['claims']) == 4
    assert [c['verdict'] for c in only(result, 'K1')] == ['range_only']
    assert [c['verdict'] for c in only(result, 'K4')] == ['match', 'absent']
    for c in result['claims']:
        assert {'report_line', 'text', 'check_type', 'verdict', 'author_marked', 'exempt', 'source', 'reason', 'reason_code'} <= c.keys()
        assert c['report_line'] == 1


@pytest.mark.parametrize('text,expected', [('일반 서술만 있습니다.', 3), ('2건 중 1건', 0), ('1건 중 2건', 1)])
def test_cli_exit_codes_and_json(tmp_path, text, expected):
    r, _ = make_case(tmp_path, text)
    result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'smtw', 'claims', 'check', '--report', str(r), '--format', 'json'], capture_output=True)
    assert result.returncode == expected, result.stderr.decode()
    assert json.loads(result.stdout)['status'] in {'PASS', 'FAIL', 'INCOMPLETE'}


def test_cli_error_code(tmp_path):
    result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'smtw', 'claims', 'check', '--report', str(tmp_path/'missing.md'), '--format', 'json'], capture_output=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)['status'] == 'ERROR'


def test_k7_verdict_tally(tmp_path):
    r, _ = make_case(tmp_path, '| ID | 판정 |\n|---|---|\n| A | PASS |\n| B | FAIL |\n\nPASS 2건, FAIL 1건')
    assert [c['verdict'] for c in run(r)['claims']] == ['mismatch', 'match']


def test_k6_line_count_uses_shared_k2_logic():
    e = Evidence(counts=line_counts('no newline'), total_lines=1)
    for kind in ('K2', 'K6'):
        c = Claim(1, '0줄', kind, value='0', unit='줄')
        assert check_one(c, e, Budget(10))[0] == 'mismatch'


def test_missing_aux_and_failed_read_precede_absence(tmp_path):
    r, t = make_case(tmp_path, 'https://example.invalid/doc', [agy_call('read_url_content', Url='https://example.invalid/doc'),
        generic('has been saved to: C:/fake-brain/steps/99/content.md')])
    assert run(r, agy=str(t))['claims'][0]['verdict'] == 'unverifiable'


def test_quoted_web_aux_content(tmp_path):
    aux = tmp_path / 'aux/steps/3'
    aux.mkdir(parents=True)
    (aux/'content.md').write_text('untrusted disk-only page text\n', encoding='utf-8')
    rows = [agy_call('read_url_content', Url='https://example.invalid/doc'),
            generic('has been saved to: C:/fake-brain/steps/3/content.md')]
    r, t = make_case(tmp_path, 'https://example.invalid/doc “exact web source phrase”', rows)
    assert run(r, agy=str(t))['status'] == 'INCOMPLETE'
    rows += [agy_call('view_file', AbsolutePath='C:/fake-brain/steps/3/content.md'),
             view('C:/fake-brain/steps/3/content.md', 'exact web source phrase')]
    write_transcript(t, rows)
    assert run(r, agy=str(t))['status'] == 'PASS'


def test_echo_appended_to_read_is_not_evidence(tmp_path):
    r, t = make_case(tmp_path, '`engine.py` 함수 `invented_name`', [
        agy_call('run_command', CommandLine='cat C:/sample/engine.py; echo "def invented_name(): pass"'),
        generic('def invented_name(): pass')])
    assert run(r, agy=str(t))['status'] == 'INCOMPLETE'


def test_metadata_does_not_invent_missing_last_nonempty_line(tmp_path):
    r, t = make_case(tmp_path, '`engine.py:2`', [agy_call('view_file', AbsolutePath='C:/sample/engine.py'),
        generic('File Path: C:/sample/engine.py\nTotal Lines: 2\nTotal Bytes: 999\nShowing lines 1 to 2\n1: x')])
    assert run(r, agy=str(t))['status'] == 'INCOMPLETE'


def test_malformed_transcript_never_passes(tmp_path):
    r, t = make_case(tmp_path, '2건 중 1건')
    t.write_text('not json\n', encoding='utf-8')
    assert run(r, agy=str(t))['status'] == 'INCOMPLETE'


def test_long_web_line_does_not_backtrack_pointer_regex(tmp_path):
    r, t = make_case(tmp_path, 'https://example.invalid/doc', [
        agy_call('read_url_content', Url='https://example.invalid/doc'), generic('/segment' * 100000)])
    start = time.monotonic()
    assert run(r, agy=str(t))['status'] == 'PASS'
    assert time.monotonic() - start < 5


def test_k8_user_and_search_text_are_not_visits(tmp_path):
    r, t = make_case(tmp_path, 'https://example.invalid/a\nhttps://example.invalid/b', [
        {'type': 'USER_INPUT', 'content': 'https://example.invalid/a'},
        agy_call('search_web', Query='documentation'), generic('https://example.invalid/b is a search hit')])
    assert [c['verdict'] for c in run(r, agy=str(t))['claims']] == ['absent', 'absent']


def test_third_party_consultation_quote_matches_without_k10(tmp_path):
    result = check(tmp_path, '`engine.py` “담당자가 유선 상담으로 확인했습니다.”', '담당자가 유선 상담으로 확인했습니다.')
    assert result['status'] == 'PASS'
    assert not result['warnings']


@pytest.mark.parametrize('count,changed,status', [(0, False, 'INCOMPLETE'), (1, False, 'PASS'),
                                               (2, False, 'INCOMPLETE'), (1, True, 'INCOMPLETE')])
def test_auto_discovery_requires_unique_exact_last_write(tmp_path, count, changed, status):
    r, _ = make_case(tmp_path, '2건 중 1건')
    brain = tmp_path/'fake-brain'
    for index in range(count):
        t = brain/f'synthetic-{index}/.system_generated/logs/transcript_full.jsonl'
        write_transcript(t, [agy_call('write_to_file', TargetFile=str(r), CodeContent='changed' if changed else r.read_text(encoding='utf-8')), generic('saved')])
    assert run(r, agy_auto=True, brain_root=brain)['status'] == status


def test_fixed_revision_is_a_sha_not_a_git_option(tmp_path):
    report, _ = make_case(tmp_path, '`engine.py:1`')
    with pytest.raises(ValueError, match='SHA'):
        run(report, commits={str(tmp_path): '--output=unexpected-file'})
    assert not (tmp_path/'unexpected-file').exists()


@pytest.mark.parametrize('command,report,output,verdict', [
    ('wc -l C:/sample/engine.py', '`engine.py` wc -l 0줄', '0 C:/sample/engine.py', 'match'),
    ('wc -l C:/sample/engine.py', '`engine.py` 0줄', '0 C:/sample/engine.py', 'unverifiable'),
    ('wc -c C:/sample/engine.py', '`engine.py` 42 B', '42 C:/sample/engine.py', 'match'),
    ('stat -c %s C:/sample/engine.py', '`engine.py` 43 B', '42', 'mismatch'),
])
def test_command_file_measurements_keep_newline_and_byte_units_separate(tmp_path, command, report, output, verdict):
    r, t = make_case(tmp_path, report, [agy_call('run_command', CommandLine=command), generic(output)])
    assert only(run(r, agy=str(t)), 'K2')[0]['verdict'] == verdict


def test_claims_entrypoint_does_not_import_legacy_modules(tmp_path):
    r, _ = make_case(tmp_path, '2건 중 1건')
    code = ('import sys; from smtw.cli import main; '
            'sys.argv=["smtw","claims","check","--report",sys.argv[1]]; '
            'main(); assert not any(n.startswith(("core.","adapters.","goals.")) for n in sys.modules)')
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, str(r)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
