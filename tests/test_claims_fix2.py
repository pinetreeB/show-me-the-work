"""Synthetic boundary cases for CLI errors, partial evidence and advisory URLs."""
from __future__ import annotations

import argparse
from decimal import InvalidOperation
import json
import subprocess
import sys

import pytest

from smtw.claims import cli
from smtw.claims.check import check_one, run
from smtw.claims.model import Budget, Claim, Evidence
from tests.claims_support import agy_call, generic, make_case, view


def atoms(result, kind):
    return [c for c in result['claims'] if c['check_type'] == kind]


def cli_args(report, format='json'):
    parser = argparse.ArgumentParser()
    cli.add_claims_parser(parser.add_subparsers())
    return parser.parse_args(['claims', 'check', '--report', str(report), '--format', format])


@pytest.mark.parametrize('error', [InvalidOperation('bad decimal'), RuntimeError('unexpected'), KeyError('field')])
@pytest.mark.parametrize('format', ['json', 'text'])
def test_b1_unexpected_exceptions_are_execution_errors(tmp_path, monkeypatch, capsys, error, format):
    def broken(*args, **kwargs):
        raise error
    monkeypatch.setattr(cli, 'run', broken)
    assert cli.run_check(cli_args(tmp_path/'report.md', format)) == 2
    output = capsys.readouterr().out
    if format == 'json':
        result = json.loads(output)
        assert result['status'] == 'ERROR'
        assert result['claims'] == []
    else:
        assert output.startswith('전체 상태: ERROR\n')


def test_b1_keyboard_interrupt_is_not_swallowed(tmp_path, monkeypatch):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, 'run', interrupt)
    with pytest.raises(KeyboardInterrupt):
        cli.run_check(cli_args(tmp_path/'report.md'))


@pytest.mark.parametrize('text', ['`engine.py` 크기 , B', '`engine.py` 총 , 줄', '3건 중 ,건', ',건 중 2건'])
def test_b1_lone_comma_is_unverifiable_and_cli_continues(tmp_path, text):
    r, t = make_case(tmp_path, text, [agy_call('view_file', AbsolutePath='C:/sample/engine.py'),
                                    view('C:/sample/engine.py', 'print(1)\n')])
    result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'smtw', 'claims', 'check',
                             '--report', str(r), '--agy', str(t), '--format', 'json'], capture_output=True)
    assert result.returncode == 3, result.stderr.decode('utf-8')
    claims = json.loads(result.stdout)['claims']
    assert [(claim['verdict'], claim['reason_code']) for claim in claims] == [('unverifiable', 'malformed_number')]
    assert b'Traceback' not in result.stderr


@pytest.mark.parametrize('number,verdict', [(2, 'mismatch'), (3, 'unverifiable'), (4, 'match'), (5, 'mismatch')])
@pytest.mark.parametrize('kind', ['K2', 'K6'])
def test_b2_partial_last_line_unknown(kind, number, verdict):
    e = Evidence(path='file.py', total_lines=4, lines={1: 'a', 2: 'b'})
    c = Claim(1, f'{number}줄', kind, value=str(number), unit='줄')
    result = check_one(c, e, Budget(5))
    assert result.verdict == verdict
    if verdict == 'unverifiable':
        assert result.code == 'partial_range'


@pytest.mark.parametrize('context,value,verdict', [('Total Lines', 3, 'mismatch'), ('Total Lines', 4, 'match'),
                                                  ('wc -l', 3, 'unverifiable')])
def test_b2_explicit_metric_conventions_unchanged(context, value, verdict):
    e = Evidence(total_lines=4, lines={1: 'a'})
    c = Claim(1, '', 'K2', value=str(value), unit='줄', context=context)
    assert check_one(c, e, Budget(5)).verdict == verdict


@pytest.mark.parametrize('last,verdict', [('', 'match'), ('c', 'mismatch')])
def test_b2_known_last_line_keeps_existing_rule(tmp_path, last, verdict):
    text = 'a\nb\n' + last
    r, t = make_case(tmp_path, '`file.py` (2줄)', [agy_call('view_file', AbsolutePath='C:/sample/file.py'),
                                               view('C:/sample/file.py', text)])
    assert atoms(run(r, agy=str(t)), 'K2')[0]['verdict'] == verdict


@pytest.mark.parametrize('value,verdict', [(3, 'match'), (2, 'unverifiable')])
def test_b2_connected_content_line_measurement(tmp_path, value, verdict):
    r, t = make_case(tmp_path, '`file.py` (3줄)', [
        agy_call('view_file', AbsolutePath='C:/sample/file.py'), view('C:/sample/file.py', 'a\nb\nc\n', end=2),
        agy_call('run_command', CommandLine='(Get-Content C:/sample/file.py).Length'), generic(str(value))])
    assert atoms(run(r, agy=str(t)), 'K2')[0]['verdict'] == verdict


@pytest.mark.parametrize('measured,verdict', [(3, 'match'), (2, 'unverifiable')])
def test_b2_partial_view_and_connected_newline_measurement(tmp_path, measured, verdict):
    r, t = make_case(tmp_path, '`file.py` (3줄)', [
        agy_call('view_file', AbsolutePath='C:/sample/file.py'), view('C:/sample/file.py', 'a\nb\nc\n', end=2),
        agy_call('run_command', CommandLine='wc -l C:/sample/file.py'), generic(f'{measured} C:/sample/file.py')])
    claim = atoms(run(r, agy=str(t)), 'K2')[0]
    assert claim['verdict'] == verdict
    if verdict == 'match':
        assert claim['reason_code'] == 'linked_line_measurement'


@pytest.mark.parametrize('mode', ['other_file', 'failed', 'known_last_line'])
def test_b2_newline_fallback_requires_linked_success_and_unknown_last_line(tmp_path, mode):
    path = 'C:/sample/other.py' if mode == 'other_file' else 'C:/sample/file.py'
    r, t = make_case(tmp_path, '`file.py` (3줄)', [
        agy_call('view_file', AbsolutePath='C:/sample/file.py'),
        view('C:/sample/file.py', 'a\nb\nc\nd', end=4 if mode == 'known_last_line' else 2),
        agy_call('run_command', CommandLine='wc -l ' + path), generic('3 '+path, 'ERROR' if mode == 'failed' else 'DONE')])
    claim = atoms(run(r, agy=str(t)), 'K2')[0]
    assert claim['verdict'] == ('mismatch' if mode == 'known_last_line' else 'unverifiable')


@pytest.mark.parametrize('gap', ['…', '...'])
@pytest.mark.parametrize('quote,verdict', [
    ('alpha prefix GAP omega suffix', 'match'),
    ('omega suffix GAP alpha prefix', 'mismatch'),
    ('alpha prefix GAP absent suffix', 'mismatch'),
    ('alpha prefix GAP alpha prefix', 'mismatch'),
])
def test_n3_ellipsis_fragments_are_ordered(tmp_path, gap, quote, verdict):
    text = 'alpha prefix\n' + ('intervening text\n' * 600) + 'omega suffix\n'
    r, t = make_case(tmp_path, '`file.py` “' + quote.replace('GAP', gap) + '”',
                     [agy_call('view_file', AbsolutePath='C:/sample/file.py'), view('C:/sample/file.py', text)])
    assert atoms(run(r, agy=str(t)), 'K5')[0]['verdict'] == verdict


def test_n3_line_bound_prose_allows_ellipsis(tmp_path):
    r, t = make_case(tmp_path, '`file.py:1-2` “alpha prefix … omega suffix”', [
        agy_call('view_file', AbsolutePath='C:/sample/file.py'), view('C:/sample/file.py', 'alpha prefix middle\nomega suffix')])
    assert atoms(run(r, agy=str(t)), 'K1')[0]['verdict'] == 'match'


def test_n3_literal_code_ellipsis_is_not_omission():
    e = Evidence(lines={1: 'alpha prefix middle omega suffix'}, total_lines=1, complete=True)
    for kind in ('K1', 'K5'):
        c = Claim(1, '', kind, start=1 if kind == 'K1' else 0, end=1,
                  quote='alpha prefix ... omega suffix', quote_code=True)
        assert check_one(c, e, Budget(5)).verdict == 'mismatch'


def test_n3_empty_or_damaged_fragments_cannot_match():
    e = Evidence(text='alpha prefix and broken \ufffd suffix', complete=True)
    for quote in ('… ... …', 'alpha prefix … omega suffix'):
        c = Claim(1, '', 'K5', quote=quote)
        assert check_one(c, e, Budget(5)).verdict == 'unverifiable'


@pytest.mark.parametrize('gap', [' damaged \ufffd text ', '\n\ufffd\n' + ('unrelated\n' * 600)])
def test_n3_damaged_gap_between_fragments_is_unverifiable(gap):
    e = Evidence(text='alpha prefix' + gap + 'omega suffix', complete=True)
    c = Claim(1, '', 'K5', quote='alpha prefix … omega suffix')
    result = check_one(c, e, Budget(5))
    assert (result.verdict, result.code) == ('unverifiable', 'decode_damage')


def test_n4_no_transcript_no_absence_or_phantom_search_scope(tmp_path):
    repo = tmp_path/'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    (repo/'file.py').write_text('some_value = 1\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(repo), 'add', 'file.py'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture'], check=True)
    r, _ = make_case(tmp_path, '함수 `absent_helper`')
    result = run(r, repos=[str(repo)])
    claim = atoms(result, 'K4')[0]
    assert claim['verdict'] == 'unverifiable'
    assert claim['reason_code'] == 'transcript_missing'
    assert '대화 읽기 결과' not in claim['source']['search_scope']
    assert any('git grep' in scope for scope in claim['source']['search_scope'])
    assert result['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('text,status', [('https://example.invalid/unvisited', 'INCOMPLETE'),
                                       ('https://example.invalid/unvisited\n2건 중 3건', 'FAIL'),
                                       ('https://example.invalid/unvisited\n3건 중 2건', 'INCOMPLETE')])
def test_n7_url_absence_is_warning_not_failure(tmp_path, text, status):
    r, t = make_case(tmp_path, text, [agy_call('view_file', AbsolutePath='C:/sample/file.py'), view('C:/sample/file.py', 'x')])
    result = run(r, agy=str(t))
    assert atoms(result, 'K8')[0]['verdict'] == 'absent'
    assert result['status'] == status
    assert any(isinstance(w, dict) and w['check_type'] == 'K8' for w in result['warnings'])


def test_n7_url_without_transcript_has_empty_actual_scope(tmp_path):
    r, _ = make_case(tmp_path, 'https://example.invalid/unvisited')
    result = run(r)
    assert result['status'] == 'INCOMPLETE'
    assert atoms(result, 'K8')[0]['source']['search_scope'] == []


def test_n7_url_warning_real_cli_exit_three(tmp_path):
    r, t = make_case(tmp_path, 'https://example.invalid/unvisited', [
        agy_call('view_file', AbsolutePath='C:/sample/file.py'), view('C:/sample/file.py', 'x')])
    result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'smtw', 'claims', 'check', '--report', str(r),
                             '--agy', str(t), '--format', 'json'], capture_output=True)
    assert result.returncode == 3
    assert json.loads(result.stdout)['warnings'][0]['check_type'] == 'K8'
