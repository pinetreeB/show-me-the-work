from __future__ import annotations

import json

from smtw.claims.check import run
from smtw.claims.model import Budget
from smtw.claims.transcripts import js_calls, read_transcript
from tests.claims_support import make_case, write_transcript


def call(name, args, ident='call-synthetic'):
    return {'type': 'response_item', 'timestamp': '2026-01-01T00:00:00Z',
            'payload': {'type': 'function_call', 'name': name, 'call_id': ident,
                        'arguments': json.dumps(args)}}


def output(value, ident='call-synthetic'):
    return {'type': 'response_item', 'payload': {'type': 'function_call_output',
                                               'call_id': ident, 'output': value}}


def test_codex_native_read_and_final_answer_excluded(tmp_path):
    r, t = make_case(tmp_path, '`engine.py:2` “return valid_value”', [
        call('exec_command', {'cmd': 'Get-Content C:/sample/engine.py'}),
        output(json.dumps({'output': 'def valid_name():\nreturn valid_value\n', 'exit_code': 0})),
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                                            'content': [{'type': 'output_text', 'text': 'made up content'}]}}])
    result = run(r, codex=t)
    assert result['claims'][0]['verdict'] == 'match'
    assert result['transcript_format'] == 'codex/jsonl-v1'


def test_codex_apply_patch_and_self_read_excluded(tmp_path):
    r, t = make_case(tmp_path, '`engine.py` 함수 `invented_name`', [
        {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'name': 'apply_patch',
           'call_id': 'patch-synthetic', 'input': '*** Begin Patch\n*** Add File: C:/sample/engine.py\n+def invented_name(): pass\n*** End Patch'}},
        output('success', 'patch-synthetic'),
        call('exec_command', {'cmd': 'Get-Content C:/sample/engine.py'}),
        output(json.dumps({'output': 'def invented_name(): pass', 'exit_code': 0}))])
    assert run(r, codex=t)['claims'][0]['verdict'] == 'unverifiable'


def test_codex_custom_exec_structured_batched_results(tmp_path):
    command_a = 'Get-Content C:/sample/first.py'
    command_b = 'Get-Content C:/sample/second.py'
    code = f'const x=await Promise.all([tools.exec_command({{cmd:{json.dumps(command_a)}}}),tools.exec_command({{cmd:{json.dumps(command_b)}}})]);x.forEach((v,i)=>text({{i,value:v}}));'
    row = {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'name': 'exec',
                                              'call_id': 'batched-synthetic', 'input': code}}
    outs = ''.join(json.dumps({'i': i, 'status': 'fulfilled', 'value': {'output': text, 'exit_code': 0}})
                   for i, text in [(0, 'def first_name(): pass'), (1, 'def second_name(): pass')])
    r, t = make_case(tmp_path, '`first.py` 함수 `first_name`\n`second.py` 함수 `second_name`', [row, output(outs, 'batched-synthetic')])
    assert run(r, codex=t)['status'] == 'PASS'


def test_codex_unmatched_output_does_not_prove_claim(tmp_path):
    r, t = make_case(tmp_path, '`engine.py` 함수 `invented_name`', [
        call('exec_command', {'cmd': 'echo "def invented_name(): pass"'}),
        output('def invented_name(): pass', 'different-call')])
    assert run(r, codex=t)['claims'][0]['verdict'] != 'match'


def test_codex_truncated_output_is_unverifiable(tmp_path):
    r, t = make_case(tmp_path, '`engine.py` 함수 `invented_name`', [
        call('exec_command', {'cmd': 'Get-Content C:/sample/engine.py'}),
        output('Warning: truncated output\ndef invented_name(): pass')])
    assert run(r, codex=t)['status'] == 'INCOMPLETE'


def test_codex_web_open_requires_result_url(tmp_path):
    r, t = make_case(tmp_path, 'https://example.invalid/doc', [
        call('web.run', {'open': [{'ref_id': 'https://example.invalid/doc'}]}),
        output('Example page (https://example.invalid/doc)\nRead result text')])
    assert run(r, codex=t)['status'] == 'PASS'


def test_codex_web_literal_wrapper_and_failed_open(tmp_path):
    row = {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'web-synthetic',
           'input': 'text(await tools.web__run({open:[{ref_id:"https://example.invalid/doc"}],response_length:"short"}));'}}
    r, t = make_case(tmp_path, 'https://example.invalid/doc', [row, output('Error: could not open https://example.invalid/doc', 'web-synthetic')])
    assert run(r, codex=t)['status'] == 'INCOMPLETE'


def test_codex_wrapped_patch_marks_future_reads(tmp_path):
    patch = '*** Begin Patch\n*** Add File: C:/sample/engine.py\n+def invented_name(): pass\n*** End Patch'
    row = {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'patch-wrapper',
           'input': f'text(await tools.apply_patch({json.dumps(patch)}));'}}
    r, t = make_case(tmp_path, '`engine.py` 함수 `invented_name`', [row, output('Done', 'patch-wrapper'),
        call('exec_command', {'cmd': 'Get-Content C:/sample/engine.py'}), output('def invented_name(): pass')])
    assert run(r, codex=t)['status'] == 'INCOMPLETE'


def test_codex_multi_web_pages_do_not_cross_match_quotes(tmp_path):
    r, t = make_case(tmp_path, 'https://example.invalid/a “second page quotation”', [
        call('web.run', {'open': [{'ref_id': 'https://example.invalid/a'}, {'ref_id': 'https://example.invalid/b'}]}),
        output('First (https://example.invalid/a)\nfirst page quotation\nSecond (https://example.invalid/b)\nsecond page quotation')])
    result = run(r, codex=t)
    quote = next(c for c in result['claims'] if c['check_type'] == 'K5')
    assert quote['verdict'] == 'mismatch'


def test_literal_wrapper_call_order():
    code = 'text(await tools.web__run({open:[{ref_id:"https://example.invalid/a"}]})); text(await tools.exec_command({cmd:"cat C:/sample/a.py"}));'
    assert [c.name for c in js_calls(code, 1, '')] == ['web__run', 'exec_command']


def test_generated_literal_mixed_with_file_read_is_excluded(tmp_path):
    r, t = make_case(tmp_path, '`engine.py` 함수 `invented_name`', [
        call('exec_command', {'cmd': 'python -c "from pathlib import Path; Path(\'C:/sample/engine.py\').read_text(); print(\'def invented_name(): pass\')"'}),
        output('def invented_name(): pass')])
    assert run(r, codex=t)['claims'][0]['verdict'] == 'unverifiable'
