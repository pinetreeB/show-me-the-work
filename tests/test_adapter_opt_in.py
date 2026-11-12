from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from adapters.claude_code.project_config import config_state
from adapters.opt_in import supervision_enabled


ROOT = Path(__file__).resolve().parents[1]
CODEX = [(f'adapters/codex_cli/{name}.py', None) for name in
         ('session_start', 'user_prompt_submit', 'pre_tool_use', 'post_tool_use', 'stop')]
AGY = [('adapters/antigravity/oma_hook.py', event) for event in
       ('PreInvocation', 'PreToolUse', 'PostToolUse', 'Stop', 'BeforeModel', 'BeforeTool', 'AfterTool', 'AfterAgent')]
QWEN = [('adapters/qwen_code/qwen_hook.py', event) for event in
        ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop', 'SessionEnd')]


def payload(root):
    return {'cwd': str(root), 'project_root': str(root), 'workspacePaths': [str(root)],
            'session_id': 'synthetic-session', 'conversationId': 'synthetic-conversation',
            'turn_id': 'synthetic-turn', 'prompt_id': 'synthetic-turn',
            'prompt': 'README.md 문구를 수정해 주세요.', 'userPrompt': 'README.md 문구를 수정해 주세요.',
            'tool_name': 'Write', 'tool_input': {'file_path': str(root/'sample.py'), 'content': 'pass'},
            'tool_response': {'success': True}, 'last_assistant_message': '완료했습니다.'}


def invoke(root, script, event):
    env = os.environ.copy()
    env.pop('SMTW_TEST_FORCE_ENABLE', None)
    args = [sys.executable, str(ROOT/script)] + ([event] if event else [])
    return subprocess.run(args, input=json.dumps(payload(root)).encode(), capture_output=True,
                          cwd=root, env=env, timeout=30)


@pytest.mark.parametrize('script,event', CODEX + AGY + QWEN)
@pytest.mark.parametrize('configuration', ['absent', 'false'])
def test_all_disabled_entrypoints_leave_no_state(tmp_path, script, event, configuration):
    if configuration == 'false':
        (tmp_path/'.smtw.toml').write_text('schema_version = 1\nsupervision = false\n', encoding='utf-8')
    before = sorted(p.name for p in tmp_path.iterdir())
    result = invoke(tmp_path, script, event)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert json.loads(result.stdout) in ({}, {'decision': 'allow'})
    assert not (tmp_path/'.smtw').exists()
    assert not (tmp_path/'.fable-lite').exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == before


@pytest.mark.parametrize('script,event', [CODEX[1], AGY[0], QWEN[1]])
def test_enabled_adapters_create_turn_state(tmp_path, script, event):
    (tmp_path/'.smtw.toml').write_text('schema_version = 1\nsupervision = true\n', encoding='utf-8')
    result = invoke(tmp_path, script, event)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert json.loads(result.stdout)
    assert (tmp_path/'.smtw').is_dir()
    assert list((tmp_path/'.smtw').rglob('*'))


@pytest.mark.parametrize('module,handler', [
    ('adapters.antigravity.oma_hook', 'handle_pre_tool_use'),
    ('adapters.antigravity.oma_hook', 'handle_post_tool_use'),
    ('adapters.antigravity.oma_hook', 'handle_pre_invocation'),
    ('adapters.antigravity.oma_hook', 'handle_stop'),
    ('adapters.qwen_code.qwen_hook', 'handle_user_prompt_submit'),
    ('adapters.qwen_code.qwen_hook', 'handle_pre_tool_use'),
    ('adapters.qwen_code.qwen_hook', 'handle_post_tool_use'),
    ('adapters.qwen_code.qwen_hook', 'handle_stop'),
    ('adapters.qwen_code.qwen_hook', 'handle_session_start'),
    ('adapters.qwen_code.qwen_hook', 'handle_session_end'),
])
def test_direct_handlers_also_gate_before_core(tmp_path, monkeypatch, module, handler):
    monkeypatch.delenv('SMTW_TEST_FORCE_ENABLE', raising=False)
    mod = importlib.import_module(module)
    emitted = []
    monkeypatch.setattr(mod, 'emit', lambda value: emitted.append(value) or 0)
    import builtins
    original = builtins.__import__
    def guard(name, *args, **kwargs):
        assert not name.startswith('core.'), 'disabled handler entered core'
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guard)
    assert getattr(mod, handler)(payload(tmp_path)) == 0
    assert emitted[0] in ({}, {'decision': 'allow'})
    assert not (tmp_path/'.smtw').exists()


@pytest.mark.parametrize('text', ['schema_version=1\nsupervision="true"', 'supervision=true',
                                'schema_version=1\nsupervision=false', 'invalid toml = ['])
def test_shared_opt_in_uses_claude_validation(tmp_path, monkeypatch, text):
    monkeypatch.delenv('SMTW_TEST_FORCE_ENABLE', raising=False)
    (tmp_path/'.smtw.toml').write_text(text, encoding='utf-8')
    assert supervision_enabled(tmp_path) == config_state(tmp_path)[0] is False
    assert not (tmp_path/'.smtw').exists()


def test_enabled_session_start_is_noop(tmp_path):
    (tmp_path/'.smtw.toml').write_text('schema_version=1\nsupervision=true', encoding='utf-8')
    result = invoke(tmp_path, *CODEX[0])
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}
    assert not (tmp_path/'.smtw').exists()


def test_session_start_malformed_payload_is_noop(tmp_path):
    env = os.environ.copy()
    env.pop('SMTW_TEST_FORCE_ENABLE', None)
    result = subprocess.run([sys.executable, str(ROOT/'adapters/codex_cli/session_start.py')],
                            input=b'not json', capture_output=True, cwd=tmp_path, env=env, timeout=30)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}
    assert not (tmp_path/'.smtw').exists()
