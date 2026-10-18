from __future__ import annotations

import warnings

import pytest

from smtw.claims.model import Budget
from smtw.claims.transcripts import (
    Call,
    command_has_write,
    command_write_targets,
    read_transcript,
    shell_wrapper_bodies,
)
from tests.claims_support import agy_call, generic, make_case


TARGET = 'C:/fixture/project/engine.py'
OTHER = 'C:/fixture/project/output.txt'


WRITE_COMMANDS = [
    f'bash -c "echo x > {TARGET}"',
    f"sh -c 'echo x >> {TARGET}'",
    f'cmd /c "echo x>{TARGET}"',
    f'powershell -NoProfile -Command "\'x\' > {TARGET}"',
    'bash -lc "git apply C:/fixture/project/fix.patch"',
    'pwsh -Command "patch -p1 -i fix.patch"',
    f'cd C:/fixture/project && bash -c "echo x > {TARGET}"',
    f"echo start; sh -c 'echo x >> {TARGET}'",
    f'echo hi | cmd /c "echo x>{TARGET}"',
    f'Write-Host start; powershell -Command "\'x\' > {TARGET}"',
    f'sudo bash -c "echo x > {TARGET}"',
    f'bash -ec "echo x > {TARGET}"',
    f'bash --login -c "echo x > {TARGET}"',
    f'powershell -c "\'x\' > {TARGET}"',
    f'pwsh -c "\'x\' > {TARGET}"',
    f'cmd.exe /d /s /c "echo x>{TARGET}"',
    f'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "\'x\' > {TARGET}"',
    f'bash -lc "powershell -Command \\\"\'x\' > {TARGET}\\\""',
    'bash -c "cd C:/fixture/project && git apply fix.patch"',
    'bash -c "git -C C:/fixture/project apply fix.patch"',
    f'python -c "open(\'{TARGET}\',\'w\').write(\'x\')"',
    ['bash', '-lc', f'echo x > {TARGET}'],
    ['powershell', '-Command', f'Set-Content -Path {TARGET} -Value x'],
    ['bash', '-lc', 'git apply C:/fixture/project/fix.patch'],
]


@pytest.mark.parametrize('command', WRITE_COMMANDS)
def test_fix5_public_b5_write_matrix_excludes_later_self_read(command, tmp_path):
    report, transcript = make_case(tmp_path, '`engine.py` function `fixture_symbol`', [
        agy_call('run_command', CommandLine=command), generic('done'),
        agy_call('run_command', CommandLine=f'cat {TARGET}'),
        generic('def fixture_symbol(): pass'),
    ])
    call = Call('run_command', {'CommandLine': command}, 1, '')
    assert call.write is True
    parsed = read_transcript(
        transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10),
    )
    assert not parsed.evidence


@pytest.mark.parametrize('command', [
    f'bash -c "cat {TARGET} 2>/dev/null"',
    f'powershell -NoProfile -Command "Get-Content {TARGET} 2>$null"',
    f"bash -c 'cat {TARGET}; git log --format=\"%h -> %s\" -1'",
    f'cmd /c "type {TARGET}"',
    f'powershell -Command "Select-String -Pattern \'patch\' {TARGET} | Out-Null; Get-Content {TARGET}"',
])
def test_fix5_public_b5_read_only_wrappers_keep_evidence(command, tmp_path):
    report, transcript = make_case(tmp_path, '`engine.py` function `fixture_symbol`', [
        agy_call('run_command', CommandLine=command), generic('def fixture_symbol(): pass'),
    ])
    call = Call('run_command', {'CommandLine': command}, 1, '')
    assert call.write is False
    parsed = read_transcript(
        transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10),
    )
    assert parsed.evidence


def test_fix5_public_b5_other_target_does_not_hide_engine_evidence(tmp_path):
    command = f'bash -c "echo x > {OTHER}"'
    report, transcript = make_case(tmp_path, '`engine.py` function `fixture_symbol`', [
        agy_call('run_command', CommandLine=command), generic('done'),
        agy_call('run_command', CommandLine=f'cat {TARGET}'),
        generic('def fixture_symbol(): pass'),
    ])
    call = Call('run_command', {'CommandLine': command}, 1, '')
    assert command_write_targets(call) == ([OTHER], False)
    parsed = read_transcript(
        transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10),
    )
    assert parsed.evidence


def test_fix5_public_b5_encoded_command_stays_out_of_scope(tmp_path):
    command = 'powershell -EncodedCommand ZQBjAGgAbwAgAHgAIAA+ACAAZQBuAGcAaQBuAGUALgBwAHkA'
    report, transcript = make_case(tmp_path, '`engine.py` function `fixture_symbol`', [
        agy_call('run_command', CommandLine=command), generic('done'),
        agy_call('run_command', CommandLine=f'cat {TARGET}'),
        generic('def fixture_symbol(): pass'),
    ])
    assert Call('run_command', {'CommandLine': command}, 1, '').write is False
    parsed = read_transcript(
        transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10),
    )
    assert parsed.evidence


@pytest.mark.parametrize('command', [
    f'bash -c "echo x > {TARGET}"',
    f'echo ready\nbash -c "echo x > {TARGET}"',
    f'echo ready; sh -c "echo x > {TARGET}"',
    f'echo ready && bash -c "echo x > {TARGET}"',
    f'false || bash -c "echo x > {TARGET}"',
    f'echo ready | cmd /k "echo x>{TARGET}"',
    f'echo ready & pwsh -Command: "\'x\' > {TARGET}"',
])
def test_fix5_each_real_command_position_finds_wrapper(command):
    assert command_has_write(command) is True
    assert command_write_targets(Call('shell', {'command': command}, 1, '')) == ([TARGET], False)


@pytest.mark.parametrize('prefix', [
    'sudo',
    'env MODE=fixture',
    'exec',
    'time',
    'nohup',
    'env MODE=fixture time',
])
def test_fix5_supported_prefixes_find_only_the_following_wrapper(prefix):
    command = f'{prefix} bash -xc "echo x > {TARGET}"'
    assert shell_wrapper_bodies(command) == [f'echo x > {TARGET}']
    assert command_write_targets(Call('shell', {'command': command}, 1, '')) == ([TARGET], False)


@pytest.mark.parametrize(('prefix', 'option', 'value'), [
    ('sudo', '-u', 'fixture'),
    ('sudo', '--user', '"fixture"'),
    ('env', '-u', 'KEY'),
    ('env', '--unset', '"KEY"'),
    ('exec', '-a', 'label'),
    ('time', '-f', '"%e"'),
    ('time', '--format', '%e'),
])
def test_fix5_prefix_options_consume_their_plain_or_quoted_value(prefix, option, value):
    command = f'{prefix} {option} {value} bash -c "echo x > {TARGET}"'
    assert shell_wrapper_bodies(command) == [f'echo x > {TARGET}']
    assert command_write_targets(Call('shell', {'command': command}, 1, '')) == ([TARGET], False)


@pytest.mark.parametrize('command', [
    ['sudo', '-u', 'fixture', 'bash', '-c', f'echo x > {TARGET}'],
    ['env', '--unset', 'KEY', 'bash', '-c', f'echo x > {TARGET}'],
    ['exec', '-a', 'label', 'bash', '-c', f'echo x > {TARGET}'],
    ['time', '--format', '%e', 'bash', '-c', f'echo x > {TARGET}'],
])
def test_fix5_list_prefix_options_match_string_option_consumption(command):
    list_call = Call('shell', {'command': command}, 1, '')
    string_call = Call('shell', {'command': ' '.join(command)}, 1, '')
    assert command_write_targets(list_call) == command_write_targets(string_call) == ([TARGET], False)


@pytest.mark.parametrize('command', [
    f'sudo --help bash -c "echo x > {TARGET}"',
    f'env --version bash -c "echo x > {TARGET}"',
    f'time --help bash -c "echo x > {TARGET}"',
    ['nohup', '--version', 'bash', '-c', f'echo x > {TARGET}'],
])
def test_fix5_terminal_prefix_options_do_not_execute_following_wrapper(command):
    assert shell_wrapper_bodies(str(command) if isinstance(command, list) else command) == []
    assert Call('shell', {'command': command}, 1, '').write is False


@pytest.mark.parametrize('command', [
    f'BASH -EC "echo x > {TARGET}"',
    f'CMD.EXE /D /S /K "echo x>{TARGET}"',
    f'PoWeRsHeLl -C "\'x\' > {TARGET}"',
    f'PWSH -CoMmAnD: "\'x\' > {TARGET}"',
    f'pwsh -Command:"\'x\' > {TARGET}"',
    f'powershell -ExecutionPolicy "Bypass" -Command "\'x\' > {TARGET}"',
])
def test_fix5_wrapper_names_and_documented_flags_are_case_insensitive(command):
    assert command_has_write(command) is True
    assert command_write_targets(Call('shell', {'command': command}, 1, '')) == ([TARGET], False)


@pytest.mark.parametrize('command', [
    ['env', 'MODE=fixture', 'bash', '-ec', f'echo x > {TARGET}'],
    ['exec', 'cmd.exe', '/k', f'echo x>{TARGET}'],
    ['nohup', 'PowerShell.exe', '-Command:', f"'x' > {TARGET}"],
])
def test_fix5_list_and_string_prefix_forms_are_equivalent(command):
    string_command = ' '.join(f'"{part}"' if ' ' in part else part for part in command)
    list_call = Call('shell', {'command': command}, 1, '')
    string_call = Call('shell', {'command': string_command}, 1, '')
    assert list_call.write is True
    assert string_call.write is True
    assert command_write_targets(list_call) == command_write_targets(string_call) == ([TARGET], False)


def test_fix5_list_powershell_command_colon_can_carry_attached_body():
    command = ['pwsh', f'-Command:echo x > {TARGET}']
    call = Call('shell', {'command': command}, 1, '')
    assert call.write is True
    assert command_write_targets(call) == ([TARGET], False)


@pytest.mark.parametrize('command', [
    ['pwsh', '-ExecutionPolicy', '-Command:literal', '-Command', f'echo x > {TARGET}'],
    f'pwsh -ExecutionPolicy -Command:literal -Command "echo x > {TARGET}"',
    f'pwsh -c "echo x > {TARGET}" -Command:"echo x > {OTHER}"',
])
def test_fix5_only_the_matched_powershell_flag_supplies_the_body(command):
    text = str(command) if isinstance(command, list) else command
    assert shell_wrapper_bodies(text) == [f'echo x > {TARGET}']
    assert command_write_targets(Call('shell', {'command': command}, 1, '')) == ([TARGET], False)


def _nested_bash(body: str, levels: int) -> str:
    for _ in range(levels):
        body = f'bash -c "{body.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
    return body


def test_fix5_escaped_nested_wrappers_stop_after_three_levels():
    body = f'echo x > {TARGET}'
    three = _nested_bash(body, 3)
    four = _nested_bash(body, 4)
    assert command_has_write(three) is True
    assert command_write_targets(Call('shell', {'command': three}, 1, '')) == ([TARGET], False)
    assert command_has_write(four) is False


def test_fix5_nested_unquoting_preserves_windows_path_backslashes_without_warnings():
    target = r'C:\fixture\project\engine.py'
    command = _nested_bash(f'echo x > {target}', 3)
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter('always', SyntaxWarning)
        assert command_write_targets(Call('shell', {'command': command}, 1, '')) == ([target], False)
    assert not [warning for warning in emitted if issubclass(warning.category, SyntaxWarning)]


@pytest.mark.parametrize('command', [
    f'echo "bash -c \'echo x > {TARGET}\'"',
    f'echo sudo bash -c "echo x > {TARGET}"',
    f'env echo bash -c "echo x > {TARGET}"',
    f'env "echo" bash -c "echo x > {TARGET}"',
    f'sudo "echo" bash -c "echo x > {TARGET}"',
    f'bash fixture.sh -c "echo x > {TARGET}"',
    f'cmd fixture.bat /c "echo x>{TARGET}"',
    f'powershell -File fixture.ps1 -Command "\'x\' > {TARGET}"',
    f'bash.txt -c "echo x > {TARGET}"',
    f'zsh -c "echo x > {TARGET}"',
    f'wsl bash -c "echo x > {TARGET}"',
    f'powershell -com "\'x\' > {TARGET}"',
    f'echo ready 2>&1 bash -c "echo x > {TARGET}"',
    f'echo ready &> bash -c "echo x > {TARGET}"',
])
def test_fix5_wrapper_lookalikes_are_not_opened(command):
    assert shell_wrapper_bodies(command) == []


@pytest.mark.parametrize('separator', [';', '|', '&', '&&', '||', '\n'])
def test_fix5_backslash_escaped_operators_do_not_create_command_positions(separator):
    command = f'echo ready \\{separator} bash -c "echo x > {TARGET}"'
    assert shell_wrapper_bodies(command) == []
    assert command_has_write(command) is False


def test_fix5_inner_and_outer_literal_write_targets_are_merged():
    outer = 'C:/fixture/project/wrapper.log'
    command = f'bash -c "echo x > {TARGET}" > {outer}'
    assert command_write_targets(Call('shell', {'command': command}, 1, '')) == (
        [TARGET, outer], False,
    )
