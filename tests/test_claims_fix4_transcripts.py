from __future__ import annotations

import warnings

import pytest

from smtw.claims.model import Budget
from smtw.claims.transcripts import Call, command_write_targets, read_transcript
from tests.claims_support import agy_call, generic, make_case


TARGET = 'C:/fixture/project/engine.py'


@pytest.mark.parametrize(('command', 'targets', 'dynamic'), [
    (f'bash -c "echo x > {TARGET}"', [TARGET], False),
    (f"sh -c 'echo x >> {TARGET}'", [TARGET], False),
    (f'CMD /C "echo x>{TARGET}"', [TARGET], False),
    (f'PoWeRsHeLl -NoProfile -cOmMaNd "\'x\' > {TARGET}"', [TARGET], False),
    ('bash -lc "git apply C:/fixture/project/fix.patch"', [], True),
    ('pwsh -Command "patch -p1 -i fix.patch"', [], True),
    (f'powershell -Command "Set-Content -Path {TARGET} -Value x"', [TARGET], False),
    (f'bash -c "printf x | tee {TARGET}"', [TARGET], False),
    (f'bash -c "sed -i s/a/b/ {TARGET}"', [TARGET], False),
    (f'python -c "open(\'{TARGET}\',\'w\').write(\'x\')"', [TARGET], False),
])
def test_fix4_wrapped_write_forms_keep_no_later_self_read(command, targets, dynamic, tmp_path):
    report, transcript = make_case(tmp_path, '`engine.py` function `read_ok`', [
        agy_call('run_command', CommandLine=command), generic('done'),
        agy_call('run_command', CommandLine=f'cat {TARGET}'), generic('def read_ok(): pass'),
    ])
    call = Call('run_command', {'CommandLine': command}, 1, '')
    assert call.write is True
    assert command_write_targets(call) == (targets, dynamic)
    parsed = read_transcript(transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10))
    assert not parsed.evidence
    assert bool(parsed.warnings) is dynamic


@pytest.mark.parametrize(('command', 'targets', 'dynamic'), [
    (['bash', '-lc', f'echo x > {TARGET}'], [TARGET], False),
    (['PowerShell.exe', '-COMMAND', f'Set-Content -Path {TARGET} -Value x'], [TARGET], False),
    (['bash', '-lc', 'git apply C:/fixture/project/fix.patch'], [], True),
    (f"bash -lc 'echo x > {TARGET}'", [TARGET], False),
    (f'echo x > {TARGET}', [TARGET], False),
    ('git apply C:/fixture/project/fix.patch', [], True),
])
def test_fix4_list_and_string_command_writes_are_equivalent(command, targets, dynamic):
    call = Call('shell', {'command': command}, 1, '')
    assert call.write is True
    assert command_write_targets(call) == (targets, dynamic)


@pytest.mark.parametrize(('command', 'targets'), [
    (f'bash -c "echo ready" > {TARGET}', [TARGET]),
    (f'bash -c "echo x > C:/fixture/project/a.py" > {TARGET}',
     ['C:/fixture/project/a.py', TARGET]),
    (f"bash -c 'cat C:/fixture/project/input.py' > {TARGET}", [TARGET]),
    (f"bash -c 'sh -c \"echo x > {TARGET}\"'", [TARGET]),
])
def test_fix4_wrapped_and_outer_writes_keep_all_real_targets(command, targets):
    call = Call('run_command', {'CommandLine': command}, 1, '')
    assert call.write is True
    assert command_write_targets(call) == (targets, False)


def test_fix4_outer_wrapper_redirect_excludes_later_target_read(tmp_path):
    command = f'bash -c "echo ready" > {TARGET}'
    report, transcript = make_case(tmp_path, '`engine.py` function `read_ok`', [
        agy_call('run_command', CommandLine=command), generic('done'),
        agy_call('run_command', CommandLine=f'cat {TARGET}'), generic('def read_ok(): pass'),
    ])
    parsed = read_transcript(transcript, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(10))
    assert not parsed.evidence


@pytest.mark.parametrize('command', [
    'git -C C:/fixture/project apply C:/fixture/project/fix.patch',
    'git am C:/fixture/project/0001.patch',
    'echo x > $env:TEMP\\engine.py',
])
def test_fix4_minimal_global_write_extensions(command):
    call = Call('run_command', {'CommandLine': command}, 1, '')
    assert call.write is True
    assert command_write_targets(call) == ([], True)


@pytest.mark.parametrize('command', [
    f'echo "bash -c \'echo x > {TARGET}\'"',
    'echo "bash -lc \'git apply C:/fixture/project/fix.patch\'"',
    f'bash.txt -c "echo x > {TARGET}"',
    f"bash -x 'text -c echo x > {TARGET}'",
    "git log --format='%h -> %s'",
    f'cat {TARGET} 2>$null',
])
def test_fix4_wrapper_text_and_existing_nonwrite_syntax_remain_safe(command):
    assert Call('run_command', {'CommandLine': command}, 1, '').write is False


def test_fix4_shell_paths_do_not_emit_python_literal_warnings():
    target = r'C:\fixture\engine.py'
    call = Call('run_command', {'CommandLine': f'powershell -Command "\'x\' > {target}"'}, 1, '')
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter('always', SyntaxWarning)
        assert call.write is True
        assert command_write_targets(call) == ([target], False)
    assert not [warning for warning in emitted if issubclass(warning.category, SyntaxWarning)]
