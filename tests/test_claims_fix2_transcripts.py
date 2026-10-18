from __future__ import annotations

import json

import pytest

from smtw.claims.check import run
from smtw.claims.model import Budget
from smtw.claims.transcripts import read_transcript
from tests.claims_support import agy_call, generic, make_case, write_transcript


def _codex_call(name, args, ident='call-synthetic'):
    return {'type': 'response_item', 'timestamp': '2026-01-01T00:00:00Z',
            'payload': {'type': 'function_call', 'name': name, 'call_id': ident,
                        'arguments': json.dumps(args)}}


def _codex_output(value, ident='call-synthetic'):
    return {'type': 'response_item',
            'payload': {'type': 'function_call_output', 'call_id': ident, 'output': value}}


@pytest.mark.parametrize('command', [
    "sed -i 's/old_fn/new_fn/' C:/sample/engine.py",
    "perl -pi -e 's/old_fn/new_fn/' C:/sample/engine.py",
    "perl -i -p -e 's/old_fn/new_fn/' C:/sample/engine.py",
    "perl -ip -e 's/old_fn/new_fn/' C:/sample/engine.py",
    'printf new | tee C:/sample/engine.py',
    'printf new | tee -a C:/sample/copy.py C:/sample/engine.py',
    "Set-Content -Path C:/sample/engine.py -Value 'new'",
    "Set-Content -Value 'new' -Path C:/sample/engine.py",
    "Out-File -InputObject 'new' -FilePath C:/sample/engine.py",
    "printf new > C:/sample/engine.py",
    "printf new >> C:/sample/engine.py",
    'cat C:/sample/input.py > C:/sample/engine.py',
    'cat C:/sample/input.py > "C:/sample/engine.py"',
    'cat C:/sample/input.py>C:/sample/engine.py',
    "cat C:/sample/input.py >>'C:/sample/engine.py'",
    'printf new 2> C:/sample/engine.py',
])
def test_literal_self_modification_excludes_only_later_target_reads(tmp_path, command):
    report, transcript = make_case(tmp_path, '`engine.py` 함수 `old_fn`', [
        agy_call('run_command', CommandLine='cat C:/sample/engine.py'), generic('def old_fn(): pass'),
        agy_call('run_command', CommandLine=command), generic('done'),
        agy_call('run_command', CommandLine='cat C:/sample/engine.py'), generic('def new_fn(): pass'),
        agy_call('run_command', CommandLine='cat C:/sample/other.py'), generic('def other_fn(): pass'),
    ])
    parsed = read_transcript(transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10))
    assert [e.text for e in parsed.evidence] == ['def old_fn(): pass', 'def other_fn(): pass']
    assert run(report, agy=str(transcript))['status'] == 'PASS'


@pytest.mark.parametrize(('command', 'global_exclusion'), [
    ("Set-Content -Path $target -Value 'new'", False),
    ('printf new > $target', True),
    ('printf new | tee $target', False),
    ('printf new > `get_target`', False),
])
def test_dynamic_write_target_excludes_later_file_reads(tmp_path, command, global_exclusion):
    report, transcript = make_case(tmp_path, '`engine.py` 함수 `new_fn`', [
        agy_call('run_command', CommandLine=command), generic('done'),
        agy_call('run_command', CommandLine='cat C:/sample/engine.py'), generic('def new_fn(): pass'),
    ])
    parsed = read_transcript(transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10))
    if global_exclusion:
        assert not parsed.evidence
        assert parsed.warnings == ['전역 쓰기 제외(호출 순서 1)로 이후 파일 근거 1건 제외']
        assert run(report, agy=str(transcript))['status'] == 'INCOMPLETE'
    else:
        assert [e.text for e in parsed.evidence] == ['def new_fn(): pass']
        assert run(report, agy=str(transcript))['status'] == 'PASS'


@pytest.mark.parametrize('command', [
    'git apply C:/sample/change.patch',
    'patch -i C:/sample/change.patch',
])
def test_unresolved_patch_write_excludes_later_reads_conservatively(tmp_path, command):
    report, transcript = make_case(tmp_path, '`engine.py` 함수 `new_fn`', [
        agy_call('run_command', CommandLine='cat C:/sample/engine.py'), generic('def old_fn(): pass'),
        agy_call('run_command', CommandLine=command), generic('done'),
        agy_call('run_command', CommandLine='cat C:/sample/engine.py'), generic('def new_fn(): pass'),
    ])
    parsed = read_transcript(transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10))
    assert [e.text for e in parsed.evidence] == ['def old_fn(): pass']
    # The earlier observation remains available and disproves the later claim;
    # the post-patch read cannot turn that into a false PASS.
    assert run(report, agy=str(transcript))['status'] == 'FAIL'


def test_codex_mcp_result_url_establishes_only_matching_successful_visit(tmp_path):
    url = 'https://example.invalid/mcp-page'
    report, transcript = make_case(tmp_path, url, [
        _codex_call('mcp__browser__navigate', {'url': url}, 'browser-synthetic'),
        _codex_output(json.dumps({'result': {'url': url, 'title': 'Synthetic page'}, 'isError': False}),
                         'browser-synthetic'),
    ])
    assert run(report, codex=transcript)['claims'][0]['verdict'] == 'match'


def test_codex_mcp_argument_url_without_literal_result_is_not_visit(tmp_path):
    url = 'https://example.invalid/mcp-page'
    report, transcript = make_case(tmp_path, url, [
        _codex_call('mcp__browser__navigate', {'url': url}, 'browser-synthetic'),
        _codex_output(json.dumps({'result': {'title': 'Synthetic page'}, 'isError': False}),
                         'browser-synthetic'),
    ])
    parsed = read_transcript(transcript, 'codex', report, report.read_text(encoding='utf-8'), {}, Budget(10))
    assert not parsed.evidence


def test_codex_mcp_failed_result_url_is_not_successful_visit(tmp_path):
    url = 'https://example.invalid/mcp-page'
    report, transcript = make_case(tmp_path, url, [
        _codex_call('mcp__browser__navigate', {'url': url}, 'browser-synthetic'),
        _codex_output(json.dumps({'result': {'url': url}, 'isError': True}), 'browser-synthetic'),
    ])
    parsed = read_transcript(transcript, 'codex', report, report.read_text(encoding='utf-8'), {}, Budget(10))
    assert len(parsed.evidence) == 1
    assert parsed.evidence[0].problem_code == 'read_failed'
