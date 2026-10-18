from __future__ import annotations

import pytest

from smtw.claims.check import run
from smtw.claims.model import Budget
from smtw.claims.transcripts import Call, read_transcript
from tests.claims_support import agy_call, generic, make_case


def _read_case(tmp_path, command):
    report, transcript = make_case(tmp_path, '`engine.py` 함수 `read_ok`', [
        agy_call('run_command', CommandLine=command), generic('done'),
        agy_call('run_command', CommandLine='cat C:/sample/engine.py'), generic('def read_ok(): pass'),
    ])
    return report, transcript


@pytest.mark.parametrize('command', [
    'Get-Content C:/sample/engine.py 2>$null',
    'cat C:/sample/engine.py 2>/dev/null',
    'cat C:/sample/engine.py 2>NUL',
    'cat C:/sample/engine.py 2>&1',
    'rg "def f() -> int" C:/sample/engine.py',
    'rg "def f() => int" C:/sample/engine.py',
    'Write-Output "patch C:/sample/change.patch"',
    'rg "<tag> > value" C:/sample/engine.py',
    'cat C:/sample/patch-notes.md',
    'cat C:/sample/engine.py | grep -v patch',
    "git log --format='%h -> %s'",
])
def test_nonwrite_syntax_and_patch_text_keep_later_read_evidence(tmp_path, command):
    report, transcript = _read_case(tmp_path, command)
    parsed = read_transcript(transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10))
    assert parsed.evidence[-1].text == 'def read_ok(): pass'


@pytest.mark.parametrize('command', [
    'git apply C:/sample/change.patch',
    'printf new > $target',
])
def test_actual_global_write_forms_warn_with_affected_evidence_count(tmp_path, command):
    report, transcript = _read_case(tmp_path, command)
    parsed = read_transcript(transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10))
    assert not parsed.evidence
    assert parsed.warnings == ['전역 쓰기 제외(호출 순서 1)로 이후 파일 근거 1건 제외']
    assert run(report, agy=str(transcript))['status'] == 'INCOMPLETE'


def test_global_warning_does_not_count_evidence_already_locally_excluded(tmp_path):
    report, transcript = make_case(tmp_path, '`engine.py` 함수 `read_ok`', [
        agy_call('run_command', CommandLine='git apply C:/sample/change.patch'), generic('done'),
        agy_call('run_command', CommandLine='printf new > C:/sample/engine.py'), generic('done'),
        agy_call('run_command', CommandLine='cat C:/sample/engine.py'), generic('def read_ok(): pass'),
    ])
    parsed = read_transcript(transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10))
    assert not parsed.evidence
    assert not parsed.warnings


@pytest.mark.parametrize('command', [
    'git apply C:/sample/change.patch',
    'patch -i C:/sample/change.patch',
    'echo ready; patch -i C:/sample/change.patch',
    'true && git apply C:/sample/change.patch',
    'echo patch C:/sample/change.patch',
    'rg patch C:/sample/engine.py',
    'patch-notes.md C:/sample/engine.py',
    'echo "; patch -i C:/sample/change.patch"',
])
def test_patch_is_a_write_only_at_command_position(command):
    call = Call('run_command', {'CommandLine': command}, 1, '')
    assert call.write is (command in {
        'git apply C:/sample/change.patch',
        'patch -i C:/sample/change.patch',
        'echo ready; patch -i C:/sample/change.patch',
        'true && git apply C:/sample/change.patch',
    })
