"""Rev2 contracts using synthetic sources only, independent of regression labels."""
from __future__ import annotations

import json

import pytest

from smtw.claims.check import check_one, run
from smtw.claims.extract import extract
from smtw.claims.model import Budget, Claim, Evidence
from smtw.claims.transcripts import js_calls
from tests.claims_support import agy_call, generic, make_case, view, write_transcript


def atoms(result, kind):
    return [c for c in result['claims'] if c['check_type'] == kind]


def inspect(tmp_path, report, source='def realToken(): pass\nsecond source line\n', path='C:/sample/widget.py'):
    r, t = make_case(tmp_path, report, [agy_call('view_file', AbsolutePath=path), view(path, source)])
    return run(r, agy=str(t))


@pytest.mark.parametrize('location', ['1행', '1행에서', '1~2행', '1-2행', '1–2줄', 'L1', 'L1-L2', 'line 1', '줄 1', '1번째 줄'])
@pytest.mark.parametrize('token,verdict', [('realToken', 'match'), ('wrongToken', 'mismatch')])
def test_r1_locations_have_content(tmp_path, location, token, verdict):
    result = inspect(tmp_path, f'`widget.py` {location} `{token}`')
    assert atoms(result, 'K1')[0]['verdict'] == 'range_only'
    assert atoms(result, 'K4')[0]['verdict'] == ('match' if verdict == 'match' else 'absent')


@pytest.mark.parametrize('text', ['총 12줄', '`L12name`', 'version12행', '"text says 12행 here"', '`widget.py` 12 lines'])
def test_r1_non_locations_are_not_positions(text):
    claims, _ = extract(text, Budget(5))
    assert not [c for c in claims if c.check_type == 'K1']


@pytest.mark.parametrize('report', [
    '- `widget.py`\n  - 함수 `realToken`',
    '### `widget.py`\n함수 `realToken`',
    '`other.py` (설명). (`widget.py` 함수 `realToken`)',
    '| 파일 | 내용 |\n|---|---|\n| `widget.py` | 함수 `realToken` |',
    '`widget.py` 함수 `realToken`. `other.py` 설명.',
])
def test_r2_precise_scope(tmp_path, report):
    assert atoms(inspect(tmp_path, report), 'K4')[0]['verdict'] == 'match'


@pytest.mark.parametrize('report', [
    '`widget.py` 및 `other.py` 함수 `realToken`',
    '| `widget.py` | `other.py` | 함수 `realToken` |',
    '- `widget.py` 및 `other.py`\n  - 함수 `realToken`',
    '### `widget.py` 및 `other.py`\n함수 `realToken`',
])
def test_r2_multiple_binding_candidates_never_global_rescue(tmp_path, report):
    claim = atoms(inspect(tmp_path, report), 'K4')[0]
    assert (claim['verdict'], claim['reason_code']) == ('unverifiable', 'binding_ambiguous')


def test_r2_same_basename_even_same_contents_is_ambiguous(tmp_path):
    rows = []
    for path in ['C:/first/widget.py', 'C:/second/widget.py']:
        rows += [agy_call('view_file', AbsolutePath=path), view(path, 'def realToken(): pass')]
    r, t = make_case(tmp_path, '`widget.py` 함수 `realToken`', rows)
    assert atoms(run(r, agy=str(t)), 'K4')[0]['reason_code'] == 'binding_ambiguous'


def test_r2_quote_after_multiple_files_is_ambiguous(tmp_path):
    result = inspect(tmp_path, '`widget.py` 및 `other.py`\n> second source line')
    assert atoms(result, 'K5')[0]['reason_code'] == 'binding_ambiguous'


@pytest.mark.parametrize('partial,verdict', [('def realToken(): pass', 'match'), ('def wrongToken(): pass', 'unverifiable')])
def test_r2_full_and_partial_observations(tmp_path, partial, verdict):
    path = 'C:/sample/widget.py'
    rows = [agy_call('view_file', AbsolutePath=path), view(path, 'def realToken(): pass\nsecond line'),
            agy_call('view_file', AbsolutePath=path),
            generic('File Path: '+path+'\nTotal Lines: 2\nShowing lines 1 to 1\n1: '+partial)]
    r, t = make_case(tmp_path, '`widget.py` 함수 `realToken`', rows)
    assert atoms(run(r, agy=str(t)), 'K4')[0]['verdict'] == verdict


@pytest.mark.parametrize('report', ['`widget.py` (1행 `wrongToken`)',
    '| `widget.py` | 1행 `wrongToken` |', '`widget.py` L1\n`wrongToken`',
    '`widget.py` 1행\n```py\ndef wrongToken(): pass\n```'])
def test_r3_inline_next_line_and_fence(tmp_path, report):
    result = inspect(tmp_path, report)
    assert atoms(result, 'K1')[0]['verdict'] == ('mismatch' if '```' in report else 'range_only')
    if '```' not in report:
        assert atoms(result, 'K4')[0]['verdict'] == 'absent'


def test_r3_scope_does_not_attach_another_parenthesis(tmp_path):
    result = inspect(tmp_path, '`widget.py` (1행) (설명 `wrongToken`)')
    assert atoms(result, 'K1')[0]['verdict'] == 'range_only'


@pytest.mark.parametrize('tail,status', [('', 'INCOMPLETE'), ('\n2건 중 1건', 'PASS'), ('\n1건 중 2건', 'FAIL')])
def test_r3_range_only_is_neutral(tmp_path, tail, status):
    result = inspect(tmp_path, '`widget.py:1`'+tail)
    assert result['status'] == status
    assert result['summary']['match'] == (1 if status == 'PASS' else 0)
    assert result['summary']['range_only'] == 1


def test_r3_backtick_location_is_not_identifier(tmp_path):
    result = inspect(tmp_path, '`widget.py` `L1`')
    assert not atoms(result, 'K4')
    assert atoms(result, 'K1')[0]['verdict'] == 'range_only'


@pytest.mark.parametrize('reference,snippet,verdict', [
    ('widget.py:1', '1: def realToken(): pass', 'match'),
    ('widget.py:1-2', '2: second source line', 'match'),
    ('widget.py:1-2', '2: different source line', 'mismatch'),
    ('widget.py:1', '2: def realToken(): pass', 'mismatch'),
])
def test_r3_inline_numbered_snippet_prefix_is_position(tmp_path, reference, snippet, verdict):
    result = inspect(tmp_path, f'`{reference}` `{snippet}`')
    assert atoms(result, 'K1')[0]['verdict'] == verdict


def test_r3_numbered_block_compares_each_position(tmp_path):
    result = inspect(tmp_path, '`widget.py:1-2`\n```\n1: def realToken(): pass\n2: second source line\n```')
    assert atoms(result, 'K1')[0]['verdict'] == 'match'


@pytest.mark.parametrize('report', ['`widget.py` (`2`줄)', '`widget.py` `2`줄',
    '`widget.py` 2 줄', '`widget.py` 총 **2**줄', '`widget.py` 2 lines',
    '| 파일 | 줄 수 |\n|---|---|\n| `widget.py` | 2 |'])
def test_r4_markdown_file_counts(tmp_path, report):
    claim = atoms(inspect(tmp_path, report, 'one\ntwo'), 'K2')[0]
    assert claim['verdict'] == 'match'


@pytest.mark.parametrize('metric', ['1,024 바이트', '1024 B', '`1,024` bytes'])
def test_r4_bytes(tmp_path, metric):
    assert atoms(inspect(tmp_path, '`widget.py` '+metric, 'a'*1024), 'K2')[0]['verdict'] == 'match'


@pytest.mark.parametrize('report', ['2 lines', '총 `2`줄', '| 내용 | 줄 수 |\n|---|---|\n| 설명 | 2 |'])
def test_r4_without_file_no_metric(report):
    claims, _ = extract(report, Budget(5))
    assert not [c for c in claims if c.check_type == 'K2']


@pytest.mark.parametrize('context', ['commit', 'SHA', 'HEAD', 'Parent', 'parent', '부모', '커밋', '대상', 'base', 'target', 'merge'])
def test_r5_short_sha_context(context):
    claims, _ = extract(context+' abcdef123', Budget(5))
    assert [c.value for c in claims if c.check_type == 'K3'] == ['abcdef123']


@pytest.mark.parametrize('text,expected', [('abcdef123', []), ('`abcdef123`', ['abcdef123']),
    ('a'*40, ['a'*40]), ('a'*41, []), ('123456', [])])
def test_r5_sha_boundaries(text, expected):
    claims, _ = extract(text, Budget(5))
    assert [c.value for c in claims if c.check_type == 'K3'] == expected


@pytest.mark.parametrize('quote', ['“정확한 원문 인용 문장”', '"정확한 원문 인용 문장"',
    "'정확한 원문 인용 문장'", '‘정확한 원문 인용 문장’', '\n> 정확한 원문 인용 문장', '\n1: 정확한 원문 인용 문장'])
def test_r6_quote_forms(tmp_path, quote):
    assert atoms(inspect(tmp_path, '`widget.py` '+quote, '정확한 원문 인용 문장'), 'K5')[0]['verdict'] == 'match'


@pytest.mark.parametrize('text', ["'short'", '“short”', '`widget.py` 번역: "translation phrase"'])
def test_r6_short_and_translation_excluded(text):
    claims, _ = extract(text, Budget(5))
    assert not [c for c in claims if c.check_type == 'K5']


@pytest.mark.parametrize('source,quote', [('**정확한** `원문` [인용](https://example.invalid) 문장', '정확한 원문 인용 문장'),
    ('정확한 원문 인용 문장', '__정확한__ *원문* 인용 문장'), ('a_b a * b value', 'a_b a * b value'),
    ('**정확한**문장은 여기 있습니다', '정확한문장은 여기 있습니다')])
def test_r6_prose_markdown_normalization_both_sides(tmp_path, source, quote):
    result = inspect(tmp_path, '`widget.py` “'+quote+'”', source)
    assert atoms(result, 'K5')[0]['verdict'] == 'match'


@pytest.mark.parametrize('report', ['`widget.py`\n1: value = important',
    '`widget.py:1`\n```\nvalue = important\n```'])
def test_r6_code_does_not_strip_operators(tmp_path, report):
    result = inspect(tmp_path, report, 'value = **important**')
    assert any(c['verdict'] == 'mismatch' for c in result['claims'] if c['check_type'] in {'K1', 'K5'})


@pytest.mark.parametrize('token,verdict', [('enabled', 'match'), ('missing', 'absent'), ('real', 'absent')])
def test_r7_linked_plain_identifier_and_boundaries(tmp_path, token, verdict):
    result = inspect(tmp_path, f'### `widget.py`\n변수 `{token}`', 'enabled realToken')
    assert atoms(result, 'K4')[0]['verdict'] == verdict


@pytest.mark.parametrize('text', ['`enabled`', '`plain`', '`camelCase`', '`--flag`'])
def test_r7_unlinked_still_restricted(text):
    claims, _ = extract(text, Budget(5))
    assert not [c for c in claims if c.check_type == 'K4']


@pytest.mark.parametrize('report', ['`widget.py` #2 `secondToken`', '`widget.py` 항목 2 `secondToken`',
    '| 번호 | 파일 | 식별자 |\n|---|---|---|\n| 2 | `widget.py` | `secondToken` |'])
@pytest.mark.parametrize('source', ['1. firstToken\n2. secondToken',
    '| 이름 |\n|---|\n| firstToken |\n| secondToken |', '- firstToken\n- secondToken'])
def test_r8_ordinal_item(tmp_path, report, source):
    assert atoms(inspect(tmp_path, report, source), 'K9')[0]['verdict'] == 'match'


@pytest.mark.parametrize('report,verdict', [('`widget.py` #1 `secondToken`', 'mismatch'),
    ('`widget.py` #3 `secondToken`', 'absent'), ('`widget.py` #2 abcdef123', 'match')])
def test_r8_wrong_row_and_sha(tmp_path, report, verdict):
    assert atoms(inspect(tmp_path, report, '1. firstToken\n2. secondToken abcdef123'), 'K9')[0]['verdict'] == verdict


@pytest.mark.parametrize('tool', ['mcp__browser__navigate', 'mcp__browser__read_page', 'mcp__browser__snapshot', 'mcp__http__fetch'])
@pytest.mark.parametrize('kind', ['agy', 'codex', 'codex_exec'])
def test_p1_browser_success_result_url(tmp_path, tool, kind):
    url = 'https://example.invalid/page'
    if kind == 'agy':
        rows = [agy_call(tool), generic('URL: '+url+'\nPage contents')]
    else:
        name = 'functions.exec' if kind == 'codex_exec' else tool
        args = f'const r = await tools.{tool}({{}}); text(r);' if kind == 'codex_exec' else '{}'
        rows = [{'type': 'response_item', 'payload': {'type': 'function_call', 'call_id': 'x', 'name': name, 'arguments': args}},
                {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'x',
                    'output': json.dumps({'url': url, 'success': True, 'content': 'Page contents'})}}]
    r, t = make_case(tmp_path, url, rows)
    result = run(r, agy=str(t)) if kind == 'agy' else run(r, codex=t)
    assert atoms(result, 'K8')[0]['verdict'] == 'match'


@pytest.mark.parametrize('tool,body,status,verdict', [
    ('mcp__browser__search', 'URL: https://example.invalid/page', 'DONE', 'absent'),
    ('mcp__browser__navigate', 'URL: https://example.invalid/page', 'ERROR', 'unverifiable'),
    ('mcp__browser__navigate', 'done', 'DONE', 'absent'),
    ('mcp__browser__snapshot', '{"url":"https://example.invalid/page","isError":true}', 'DONE', 'unverifiable'),
])
def test_p1_search_failure_and_argument_only_are_not_visits(tmp_path, tool, body, status, verdict):
    r, t = make_case(tmp_path, 'https://example.invalid/page',
        [agy_call(tool, url='https://example.invalid/page'), generic(body, status)])
    assert atoms(run(r, agy=str(t)), 'K8')[0]['verdict'] == verdict


def test_p1_dynamic_js_is_not_evaluated():
    assert not js_calls('tools.mcp__browser__navigate({url: getUrl()})', 1, '')


def test_p1_mcp_content_container_preserves_url(tmp_path):
    rows = [{'type': 'response_item', 'payload': {'type': 'function_call', 'call_id': 'x', 'name': 'mcp__browser__snapshot', 'arguments': '{}'}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'x',
             'output': json.dumps({'isError': False, 'content': [{'type': 'text', 'text': 'URL: https://example.invalid/page\nPage contents'}]})}}]
    r, t = make_case(tmp_path, 'https://example.invalid/page', rows)
    assert run(r, codex=t)['status'] == 'PASS'


def test_p1_parallel_wrapper_keeps_mcp_result_index(tmp_path):
    code = 'await Promise.allSettled([tools.mcp__browser__navigate({}), tools.mcp__browser__snapshot({})]);'
    rows = [{'type': 'response_item', 'payload': {'type': 'function_call', 'call_id': 'x', 'name': 'functions.exec', 'arguments': code}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'x',
             'output': json.dumps([{'i': 1, 'status': 'fulfilled', 'value': {'content': [{'type': 'text', 'text': 'URL: https://example.invalid/page'}]}},
                                   {'i': 0, 'status': 'fulfilled', 'value': {'content': [{'type': 'text', 'text': 'no URL'}]}}])}}]
    r, t = make_case(tmp_path, 'https://example.invalid/page', rows)
    assert run(r, codex=t)['status'] == 'PASS'


@pytest.mark.parametrize('report', ['`widget.py` 8 MiB 제한', '`widget.py` 최대 8 MB', '`widget.py` rate: 10 ms'])
def test_p2_limits_not_file_size(report):
    claims, _ = extract(report, Budget(5))
    assert not [c for c in claims if c.check_type == 'K2']


@pytest.mark.parametrize('source,verdict', [('rate: 10 ms', 'match'), ('rate: 20 ms', 'mismatch'),
    ('rate: 10 ms\nrate: 20 ms', 'unverifiable'), ('rate: 10 ms\nrate: 10 ms', 'match')])
def test_p2_entity_value_must_be_unique(tmp_path, source, verdict):
    claim = atoms(inspect(tmp_path, '`widget.py` rate: 10 ms', source), 'K6')[0]
    assert claim['verdict'] == verdict
    if verdict == 'unverifiable':
        assert claim['reason_code'] == 'binding_ambiguous'


@pytest.mark.parametrize('suffix', [' && echo DONE', "; if ($?) {echo 'DONE'}", ' && Write-Output "DONE"'])
def test_p3_conditional_marker_is_control_not_content(tmp_path, suffix):
    rows = [agy_call('run_command', CommandLine='cat C:/sample/widget.py'+suffix), generic('realToken\nDONE\n')]
    r, t = make_case(tmp_path, '`widget.py` 변수 `realToken`, `DONE`', rows)
    assert [c['verdict'] for c in atoms(run(r, agy=str(t)), 'K4')] == ['match', 'absent']


@pytest.mark.parametrize('command', ['echo realToken', 'cat C:/sample/widget.py; echo realToken',
    'echo realToken && echo DONE', 'cat C:/sample/widget.py && echo "$invented"'])
def test_p3_unconditional_or_dynamic_echo_stays_excluded(tmp_path, command):
    r, t = make_case(tmp_path, '`widget.py` `realToken`', [agy_call('run_command', CommandLine=command), generic('realToken\nDONE\n')])
    assert atoms(run(r, agy=str(t)), 'K4')[0]['verdict'] == 'unverifiable'


def test_p3_empty_marker_is_not_silently_added_to_line_count(tmp_path):
    r, t = make_case(tmp_path, '`widget.py` 2줄', [agy_call('run_command', CommandLine='cat C:/sample/widget.py && echo ""'), generic('one\n\n')])
    assert atoms(run(r, agy=str(t)), 'K2')[0]['verdict'] == 'unverifiable'


def test_p3_conditional_marker_cannot_rescue_self_written_file(tmp_path):
    path = 'C:/sample/widget.py'
    r, t = make_case(tmp_path, '`widget.py` `realToken`', [agy_call('write_to_file', TargetFile=path, CodeContent='realToken'),
        generic('saved'), agy_call('run_command', CommandLine='cat '+path+' && echo DONE'), generic('realToken\nDONE\n')])
    assert atoms(run(r, agy=str(t)), 'K4')[0]['verdict'] == 'unverifiable'


@pytest.mark.parametrize('last,number,verdict', [('', 9, 'match'), ('', 10, 'match'), ('', 8, 'mismatch'), ('last content', 9, 'mismatch')])
def test_p4_partial_view_trailing_empty_line(tmp_path, last, number, verdict):
    r, t = make_case(tmp_path, f'`widget.py` {number}줄', [agy_call('view_file', AbsolutePath='C:/sample/widget.py'),
        generic('File Path: C:/sample/widget.py\nTotal Lines: 10\nShowing lines 9 to 10\n9: text\n10: '+last)])
    assert atoms(run(r, agy=str(t)), 'K2')[0]['verdict'] == verdict


@pytest.mark.parametrize('command', ['(Get-Content C:/sample/widget.py).Length', '@(Get-Content C:/sample/widget.py).Count'])
@pytest.mark.parametrize('number,verdict', [(7, 'match'), (8, 'mismatch')])
def test_p4_powershell_actual_line_measurement(tmp_path, command, number, verdict):
    r, t = make_case(tmp_path, f'`widget.py` {number}줄', [agy_call('run_command', CommandLine=command), generic('7\n')])
    assert atoms(run(r, agy=str(t)), 'K2')[0]['verdict'] == verdict


@pytest.mark.parametrize('measured,verdict', [(2, 'match'), (3, 'unverifiable')])
def test_p4_view_and_actual_measurement_are_combined(tmp_path, measured, verdict):
    path = 'C:/sample/widget.py'
    r, t = make_case(tmp_path, '`widget.py` 2줄', [agy_call('view_file', AbsolutePath=path), view(path, 'one\ntwo\n'),
        agy_call('run_command', CommandLine=f'(Get-Content {path}).Count'), generic(str(measured))])
    assert atoms(run(r, agy=str(t)), 'K2')[0]['verdict'] == verdict


@pytest.mark.parametrize('e,code', [(Evidence(lines={1: 'one'}, complete=False), 'partial_range'),
    (Evidence(text='damaged \ufffd', complete=True), 'decode_damage'),
    (Evidence(problem='missing pointer', problem_code='pointer_missing'), 'pointer_missing'),
    (Evidence(problem='pending task', problem_code='async_incomplete'), 'async_incomplete')])
def test_p5_reason_codes_survive_checker(e, code):
    claim = Claim(1, '`unknown`', 'K4', value='unknown')
    assert check_one(claim, e, Budget(5)).code == code


def test_p5_no_read_and_historical_codes(tmp_path):
    r, _ = make_case(tmp_path, '`widget.py:1`')
    assert run(r)['claims'][0]['reason_code'] == 'no_read_of_file'
    r.write_text('수정 전 `widget.py:1`', encoding='utf-8')
    assert run(r)['claims'][0]['reason_code'] == 'historical_no_commit'


def test_p5_actual_pointer_and_async_codes(tmp_path):
    r, t = make_case(tmp_path, '`widget.py` `missingToken`', [
        agy_call('run_command', CommandLine='cat C:/sample/widget.py'),
        generic('has been saved to: C:/fake-brain/steps/7/output.txt')])
    assert run(r, agy=str(t))['claims'][0]['reason_code'] == 'pointer_missing'
    write_transcript(t, [agy_call('run_command', CommandLine='cat C:/sample/widget.py'), generic('status: RUNNING background task task-8')])
    assert run(r, agy=str(t))['claims'][0]['reason_code'] == 'async_incomplete'
