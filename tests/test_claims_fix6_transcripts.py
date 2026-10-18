from __future__ import annotations

from time import perf_counter

import pytest

from smtw.claims import transcripts
from smtw.claims.model import Budget
from smtw.claims.transcripts import Call, command_write_targets, read_transcript
from tests.claims_support import agy_call, generic, make_case


TARGET = 'C:/fixture/project/engine.py'
OTHER = 'C:/fixture/project/output.txt'


OPTION_WRAPPED_WRITES = [
    f'bash -o pipefail -c "echo x > {TARGET}"',
    f'bash -o errexit -o nounset -c "echo x > {TARGET}"',
    f'bash -O extglob -c "echo x > {TARGET}"',
    f'bash +o nounset -c "echo x > {TARGET}"',
    f'bash +O extglob -c "echo x > {TARGET}"',
    f'bash --rcfile C:/fixture/config/bash.rc -c "echo x > {TARGET}"',
    f'bash --init-file C:/fixture/config/bash.rc -c "echo x > {TARGET}"',
    f'bash -e -u -o pipefail -c "echo x > {TARGET}"',
    f'bash +e -c "echo x > {TARGET}"',
    f'sh -o errexit -c "echo x > {TARGET}"',
    f'pwsh -NoProfileLoadTime -Command "echo x > {TARGET}"',
    f'powershell -PSConsoleFile C:/fixture/config/console.psc1 -Command "echo x > {TARGET}"',
]


@pytest.mark.parametrize('command', OPTION_WRAPPED_WRITES)
def test_fix6_value_options_before_wrapper_flags_exclude_later_self_read(command, tmp_path):
    report, transcript = make_case(tmp_path, '`engine.py` function `fixture_symbol`', [
        agy_call('run_command', CommandLine=command), generic('done'),
        agy_call('run_command', CommandLine=f'cat {TARGET}'),
        generic('def fixture_symbol(): pass'),
    ])
    call = Call('run_command', {'CommandLine': command}, 1, '')
    assert call.write is True
    assert command_write_targets(call) == ([TARGET], False)
    parsed = read_transcript(
        transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10),
    )
    assert not parsed.evidence


@pytest.mark.parametrize('command', [
    ['bash', '-o', 'pipefail', '-c', f'echo x > {TARGET}'],
    ['bash', '--rcfile', 'C:/fixture/config/bash.rc', '-c', f'echo x > {TARGET}'],
    ['pwsh', '-NoProfileLoadTime', '-Command', f'echo x > {TARGET}'],
    ['powershell', '-PSConsoleFile', 'C:/fixture/config/console.psc1', '-c',
     f'echo x > {TARGET}'],
])
def test_fix6_list_value_options_match_string_wrapper_detection(command):
    string_command = ' '.join(command)
    list_call = Call('shell', {'command': command}, 1, '')
    string_call = Call('shell', {'command': string_command}, 1, '')
    assert list_call.write is True
    assert string_call.write is True
    assert command_write_targets(list_call) == command_write_targets(string_call) == ([TARGET], False)


@pytest.mark.parametrize('command', [
    f'bash C:/fixture/project/run.sh -c "echo x > {TARGET}"',
    f'bash -o pipefail C:/fixture/project/run.sh -c "echo x > {TARGET}"',
    f'bash --rcfile C:/fixture/config/bash.rc C:/fixture/project/run.sh -c "echo x > {TARGET}"',
    f'powershell -File C:/fixture/project/run.ps1 -Command "echo x > {TARGET}"',
    f'pwsh -f C:/fixture/project/run.ps1 -c "echo x > {TARGET}"',
    'bash --version',
    f'echo bash -c "echo x > {TARGET}"',
])
def test_fix6_script_and_file_modes_do_not_open_later_command_flags(command):
    assert transcripts.shell_wrapper_bodies(command) == []
    assert Call('shell', {'command': command}, 1, '').write is False


@pytest.mark.parametrize('command', [
    f'bash -o pipefail -c "cat {TARGET}"',
    f'time bash -O extglob -c "cat {TARGET}"',
    f'sudo -u fixture bash --init-file C:/fixture/config/bash.rc -c "cat {TARGET}"',
    f'env MODE=fixture pwsh -NoProfileLoadTime -Command "Get-Content {TARGET}"',
])
def test_fix6_read_only_option_wrappers_remain_reads(command):
    assert Call('shell', {'command': command}, 1, '').write is False


def test_fix6_option_wrapper_write_to_other_file_keeps_engine_evidence(tmp_path):
    command = f'bash -o pipefail -c "echo x > {OTHER}"'
    report, transcript = make_case(tmp_path, '`engine.py` function `fixture_symbol`', [
        agy_call('run_command', CommandLine=command), generic('done'),
        agy_call('run_command', CommandLine=f'cat {TARGET}'),
        generic('def fixture_symbol(): pass'),
    ])
    assert command_write_targets(Call('shell', {'command': command}, 1, '')) == ([OTHER], False)
    parsed = read_transcript(
        transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10),
    )
    assert parsed.evidence


def test_fix6_command_positions_are_computed_once_and_500_lines_stay_fast(monkeypatch):
    original = transcripts._command_positions
    position_calls = 0

    def counted(code):
        nonlocal position_calls
        position_calls += 1
        return original(code)

    monkeypatch.setattr(transcripts, '_command_positions', counted)
    command = '\n'.join(f'echo fixture_{index}' for index in range(500))
    started = perf_counter()
    assert Call('shell', {'command': command}, 1, '').write is False
    elapsed = perf_counter() - started
    assert position_calls == 1
    assert elapsed < 0.1
