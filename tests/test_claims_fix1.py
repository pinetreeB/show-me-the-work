"""Synthetic boundary and evidence regressions for the independent review fixes."""
from __future__ import annotations

import json

import pytest

from smtw.claims.check import run
from smtw.claims.extract import extract
from smtw.claims.model import Budget
from tests.claims_support import agy_call, generic, make_case, view, write_transcript


def atoms(result, kind):
    return [c for c in result['claims'] if c['check_type'] == kind]


def inspect(tmp_path, report, source, path='C:/sample/widget.py'):
    r, t = make_case(tmp_path, report, [agy_call('view_file', AbsolutePath=path), view(path, source)])
    return run(r, agy=str(t))


@pytest.mark.parametrize('reference,positions', [('widget.py:1,3', [(1,1),(3,3)]),
    ('widget.py:1, 3-5', [(1,1),(3,5)]), ('widget.py:L1,L3~L5, 8', [(1,1),(3,5),(8,8)])])
def test_n1_comma_positions_are_separate_atoms(reference, positions):
    claims, _ = extract('`'+reference+'`', Budget(5))
    k1 = [c for c in claims if c.check_type == 'K1']
    assert [(c.start,c.end) for c in k1] == positions
    assert all(c.path == 'widget.py' for c in k1)


def test_n1_each_range_is_checked(tmp_path):
    result = inspect(tmp_path, '`widget.py:1,3,8-9`', 'first line\nsecond line\nthird line')
    assert [c['verdict'] for c in atoms(result,'K1')] == ['range_only','range_only','mismatch']


@pytest.mark.parametrize('suffix', [', 3 B', ', 3개', ', prose 3', ', other.py:3'])
def test_n1_comma_does_not_swallow_units_or_other_files(suffix):
    claims, _ = extract('widget.py:1'+suffix, Budget(5))
    assert [(c.path,c.start) for c in claims if c.check_type == 'K1' and c.path == 'widget.py'] == [('widget.py',1)]


def test_n1_numbered_quote_selects_its_range(tmp_path):
    result = inspect(tmp_path, '`widget.py:1,3` `1: first source line`', 'first source line\nsecond\nthird')
    assert [c['verdict'] for c in atoms(result,'K1')] == ['match','range_only']


@pytest.mark.parametrize('position', ['`2줄`', '2줄에서', '2줄의', '2줄은'])
def test_n2_single_line_positions_are_not_file_counts(tmp_path, position):
    result = inspect(tmp_path, '`widget.py` '+position, 'one\ntwo\nthree\nfour')
    assert not atoms(result,'K2')
    assert atoms(result,'K1')[0]['verdict'] == 'range_only'


def test_n2_position_outside_file_is_still_wrong(tmp_path):
    result = inspect(tmp_path, '`widget.py` `9줄`', 'one\ntwo')
    assert not atoms(result,'K2')
    assert atoms(result,'K1')[0]['verdict'] == 'mismatch'


@pytest.mark.parametrize('count', ['총 2줄', '전체 2줄', '(2줄)', '2줄짜리', '(`2줄`)', '총 2줄은'])
def test_n2_size_context_keeps_counts(tmp_path, count):
    result = inspect(tmp_path, '`widget.py` '+count, 'one\ntwo')
    assert atoms(result,'K2')[0]['verdict'] == 'match'
    assert not atoms(result,'K1')


def test_n2_ambiguous_bare_number_is_not_assumed_size():
    claims, _ = extract('### widget.py\n5줄', Budget(5))
    assert not [c for c in claims if c.check_type == 'K2']


def test_n2_table_count_column_is_explicit(tmp_path):
    result = inspect(tmp_path, '| 파일 | 줄 수 |\n|---|---|\n| `widget.py` | `2줄` |', 'one\ntwo')
    assert atoms(result,'K2')[0]['verdict'] == 'match'
    assert not atoms(result,'K1')


@pytest.mark.parametrize('prefix', ['', '대상: ', 'HEAD ', 'Parent ', 'base '])
def test_n3_date_shaped_numbers_are_not_shas(prefix):
    claims, _ = extract(prefix+'`20240229`', Budget(5))
    assert not [c for c in claims if c.check_type == 'K3']


@pytest.mark.parametrize('prefix', ['commit ', 'SHA: ', '해시=', '커밋 '])
def test_n3_explicit_hash_prefix_keeps_numeric_sha(prefix):
    claims, _ = extract(prefix+'`20240229`', Budget(5))
    assert [c.value for c in claims if c.check_type == 'K3'] == ['20240229']


@pytest.mark.parametrize('token', ['12345678','2024abcd','20241305','2'*40])
def test_n3_non_date_hex_is_unchanged(token):
    claims, _ = extract('`'+token+'`', Budget(5))
    assert [c.value for c in claims if c.check_type == 'K3'] == [token]


@pytest.mark.parametrize('left,right', [('"','"'), ("'","'"), ('“','”'), ('‘','’')])
def test_n4_short_pairs_do_not_create_reversed_quotes(left, right):
    claims, _ = extract(f'widget.py {left}탭{right} 따옴표 밖의 긴 설명 문장 {left}버튼{right}', Budget(5))
    assert not [c for c in claims if c.check_type == 'K5']
    claims, _ = extract(f'widget.py {left}탭{right} 설명 {left}정확한 원문 인용입니다{right} 뒤 {left}끝{right}', Budget(5))
    assert [c.quote for c in claims if c.check_type == 'K5'] == ['정확한 원문 인용입니다']


def test_n4_escaped_quote_and_code_string_boundaries():
    report = 'widget.py "탭" 설명 `requireSession("화면")` 뒤 "정확한 \\"인용\\" 원문입니다"'
    claims, _ = extract(report, Budget(5))
    assert len([c for c in claims if c.check_type == 'K5']) == 1
    assert not any('requireSession' in c.quote for c in claims if c.check_type == 'K5')


@pytest.mark.parametrize('last', ['', ' ', '\t'])
@pytest.mark.parametrize('count,verdict', [(3,'match'),(4,'match'),(2,'mismatch')])
def test_f1_empty_last_display_line_with_no_bytes(tmp_path, last, count, verdict):
    body = 'File Path: C:/sample/widget.py\nTotal Lines: 4\nShowing lines 3 to 4\n3: last text\n4: '+last
    r, t = make_case(tmp_path, f'`widget.py` 총 {count}줄', [agy_call('view_file', AbsolutePath='C:/sample/widget.py'), generic(body)])
    assert atoms(run(r, agy=str(t)), 'K2')[0]['verdict'] == verdict


def test_f1_nonempty_last_display_line_is_not_subtracted(tmp_path):
    body = 'File Path: C:/sample/widget.py\nTotal Lines: 4\nShowing lines 4 to 4\n4: last content'
    r, t = make_case(tmp_path, '`widget.py` 총 3줄', [agy_call('view_file', AbsolutePath='C:/sample/widget.py'), generic(body)])
    assert atoms(run(r, agy=str(t)), 'K2')[0]['verdict'] == 'mismatch'


def test_f1_full_display_whitespace_last_line(tmp_path):
    body = 'File Path: C:/sample/widget.py\nTotal Lines: 3\n1: first\n2: second\n3: \t'
    r, t = make_case(tmp_path, '`widget.py` 총 2줄', [agy_call('view_file', AbsolutePath='C:/sample/widget.py'), generic(body)])
    assert atoms(run(r, agy=str(t)), 'K2')[0]['verdict'] == 'match'


def test_f1_view_pointer_recovers_display_counts_and_quotes(tmp_path):
    aux = tmp_path/'aux/steps/7'
    aux.mkdir(parents=True)
    (aux/'output.txt').write_text('File Path: C:/sample/widget.py\nTotal Lines: 3\n1: first source line\n2: second line\n3: \t\n', encoding='utf-8')
    r, t = make_case(tmp_path, '`widget.py` 총 2줄\n`widget.py:1` “first source line”', [agy_call('view_file', AbsolutePath='C:/sample/widget.py'),
        generic('File Path: C:/sample/widget.py\nTotal Lines: 3\nhas been saved to: C:/fake-brain/steps/7/output.txt')])
    result = run(r, agy=str(t))
    assert atoms(result,'K2')[0]['verdict'] == 'match'
    assert atoms(result,'K1')[0]['verdict'] == 'match'


@pytest.mark.parametrize('wrong_path', [False, True])
def test_f1_missing_or_misbound_view_pointer_is_not_count_proof(tmp_path, wrong_path):
    if wrong_path:
        aux = tmp_path/'aux/steps/7'
        aux.mkdir(parents=True)
        (aux/'output.txt').write_text('File Path: C:/other/different.py\nTotal Lines: 2\n1: first\n2: ', encoding='utf-8')
    r, t = make_case(tmp_path, '`widget.py` 총 1줄', [agy_call('view_file', AbsolutePath='C:/sample/widget.py'),
        generic('has been saved to: C:/fake-brain/steps/7/output.txt')])
    assert atoms(run(r, agy=str(t)), 'K2')[0]['verdict'] == 'unverifiable'


@pytest.mark.parametrize('command,metric', [('(Get-Content C:/sample/widget.py).Length','총'), ('wc -l C:/sample/widget.py','wc -l')])
@pytest.mark.parametrize('complete,expected', [(True,'match'),(False,'unverifiable')])
def test_f1_linked_async_measurements(tmp_path, command, metric, complete, expected):
    aux = tmp_path/'aux/tasks'
    aux.mkdir(parents=True)
    (aux/'task-4.log').write_text('3\n', encoding='utf-8')
    rows = [agy_call('run_command', CommandLine=command), generic('Task logs are available at: C:/fake-brain/tasks/task-4.log')]
    if complete:
        rows.append({'type':'SYSTEM_MESSAGE','content':'Task id 4 finished with result: command exited with code 0'})
    r, t = make_case(tmp_path, f'`widget.py` {metric} 3줄', rows)
    assert atoms(run(r, agy=str(t)),'K2')[0]['verdict'] == expected


@pytest.mark.parametrize('kind', ['agy','codex'])
@pytest.mark.parametrize('suffix', [' && echo "SYNTAX_OK"', "; if ($?) {echo 'SYNTAX_OK'}"])
def test_f2_marker_proves_command_success_not_source(tmp_path, kind, suffix):
    command = 'bash -n C:/sample/check.sh'+suffix
    if kind == 'agy':
        rows = [agy_call('run_command', CommandLine=command), generic('SYNTAX_OK\n')]
    else:
        rows = [{'type':'response_item','payload':{'type':'function_call','call_id':'check','name':'exec_command','arguments':json.dumps({'cmd':command})}},
                {'type':'response_item','payload':{'type':'function_call_output','call_id':'check','output':json.dumps({'output':'SYNTAX_OK\n','exit_code':0})}}]
    r, t = make_case(tmp_path, '`SYNTAX_OK` (exit 0)\n`check.sh` `SYNTAX_OK`', rows)
    result = run(r, agy=str(t)) if kind == 'agy' else run(r, codex=t)
    assert atoms(result,'K4')[0]['verdict'] == 'match'
    assert atoms(result,'K4')[0]['reason_code'] == 'command_marker_match'
    assert atoms(result,'K4')[1]['verdict'] == 'unverifiable'


@pytest.mark.parametrize('command,body,status', [('echo SYNTAX_OK','SYNTAX_OK','DONE'),
    ('bash -n C:/sample/check.sh; echo SYNTAX_OK','SYNTAX_OK','DONE'),
    ('bash -n C:/sample/check.sh && echo SYNTAX_OK','nothing printed','DONE'),
    ('bash -n C:/sample/check.sh && echo SYNTAX_OK','SYNTAX_OK','ERROR'),
    ('bash -n C:/sample/check.sh && echo SYNTAX_OK','SYNTAX_OK\nExit code: 1','DONE')])
def test_f2_unconditional_missing_or_failed_marker_is_not_proof(tmp_path, command, body, status):
    r, t = make_case(tmp_path, '`SYNTAX_OK` (exit 0)', [agy_call('run_command', CommandLine=command), generic(body,status)])
    assert atoms(run(r, agy=str(t)),'K4')[0]['verdict'] == 'unverifiable'


@pytest.mark.parametrize('complete,expected', [(True,'match'),(False,'unverifiable')])
def test_f2_aux_marker_requires_completion(tmp_path, complete, expected):
    aux = tmp_path/'aux/tasks'
    aux.mkdir(parents=True)
    (aux/'task-5.log').write_text('SYNTAX_OK\n', encoding='utf-8')
    rows = [agy_call('run_command', CommandLine='bash -n C:/sample/check.sh && echo SYNTAX_OK'),
            generic('Task logs are available at: C:/fake-brain/tasks/task-5.log')]
    if complete:
        rows.append({'type':'SYSTEM_MESSAGE','content':'Task id 5 finished with result: command exited with code 0'})
    r, t = make_case(tmp_path, '`SYNTAX_OK` (exit 0)\n`check.sh` `SYNTAX_OK`', rows)
    result = run(r, agy=str(t))
    assert atoms(result,'K4')[0]['verdict'] == expected
    assert atoms(result,'K4')[1]['verdict'] == 'unverifiable'


def test_f2_marker_from_self_authored_file_stays_excluded(tmp_path):
    path = 'C:/sample/check.sh'
    r, t = make_case(tmp_path, '`SYNTAX_OK` (exit 0)', [agy_call('write_to_file',TargetFile=path,CodeContent='true'),generic('saved'),
        agy_call('run_command',CommandLine=f'bash -n {path} && echo SYNTAX_OK'),generic('SYNTAX_OK')])
    assert atoms(run(r, agy=str(t)),'K4')[0]['verdict'] == 'unverifiable'


@pytest.mark.parametrize('value,expected', [('19 B','match'),('20 B','mismatch')])
def test_f3_record_field_is_not_file_size(tmp_path, value, expected):
    result = inspect(tmp_path, '`record.json` 안의 `payloadBytes` 값 '+value, 'payloadBytes: 19 B', 'C:/sample/record.json')
    assert not atoms(result,'K2')
    assert atoms(result,'K6')[0]['verdict'] == expected


@pytest.mark.parametrize('value,expected', [('[19, 21]','match'),('[21, 19]','mismatch')])
def test_f3_json_array_is_a_key_value_claim(tmp_path, value, expected):
    result = inspect(tmp_path, '`record.json` 내용 `sizes: '+value+'`', '{"sizes": [19,21]}', 'C:/sample/record.json')
    assert not atoms(result,'K2')
    assert atoms(result,'K6')[0]['verdict'] == expected


@pytest.mark.parametrize('source,expected', [('{"sizes": [\n19,\n21\n]}','match'),
    ('{"sizes": [\n19,\n22\n]}','mismatch'), ('{"sizes": [\n19,','unverifiable'),
    ('{"sizes":[19,21], "sizes":[19,22]}','unverifiable')])
def test_f3_multiline_and_duplicate_json_values(tmp_path, source, expected):
    result = inspect(tmp_path, '`record.json` 내용 `sizes: [19,21]`', source, 'C:/sample/record.json')
    assert atoms(result,'K6')[0]['verdict'] == expected


def test_f3_content_without_key_is_not_promoted_to_file_size(tmp_path):
    result = inspect(tmp_path, '`record.json` 안의 크기는 19 B', 'other content', 'C:/sample/record.json')
    assert not atoms(result,'K2')


@pytest.mark.parametrize('delta,expected', [(0,'match'),(1,'mismatch')])
def test_f3_explicit_file_size_still_checks(tmp_path, delta, expected):
    source = '{"sizes": [19,21]}'
    result = inspect(tmp_path, f'`record.json` 크기 {len(source.encode())+delta} B', source, 'C:/sample/record.json')
    assert atoms(result,'K2')[0]['verdict'] == expected


@pytest.mark.parametrize('quote,expected', [('exact source phrase','match'),('other source phrase','mismatch')])
def test_f4_long_source_line_checks_before_trimming_window(tmp_path, quote, expected):
    result = inspect(tmp_path, '`widget.py` “'+quote+'”', 'prefix exact source phrase '+('x'*9000))
    assert atoms(result,'K5')[0]['verdict'] == expected


@pytest.mark.parametrize('source,quote,expected', [('prefix **정확한** `원문` 문장 suffix','정확한 원문 문장','match'),
    ('prefix **정확한** `원문` 문장 suffix','틀린 다른 원문 문장','mismatch')])
def test_f4_k1_quoted_prose_uses_v6_normalization(tmp_path, source, quote, expected):
    result = inspect(tmp_path, '`widget.py:1` “'+quote+'”', source)
    assert atoms(result,'K1')[0]['verdict'] == expected
    assert atoms(result,'K5')[0]['verdict'] == expected


def test_f4_code_block_keeps_operator_meaning(tmp_path):
    result = inspect(tmp_path, '`widget.py:1`\n```\nvalue = important\n```', 'value = **important**')
    assert atoms(result,'K1')[0]['verdict'] == 'mismatch'
