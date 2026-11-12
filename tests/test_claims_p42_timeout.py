"""Controlled deadline contracts, separate from real parser correctness budgets."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from smtw.claims import js_definitions as js
from smtw.claims.model import Claim
from smtw.claims.paths import normalize_path


def claim():
    return Claim(1, 'synthetic', 'K4', path='logic.py', value='moved_helper',
                 definition=True, verdict='absent', reason_code='definition_missing',
                 source={'synthetic': 'original'})


@pytest.mark.parametrize('outer,seconds', [(0.5, None), (1, None), (3, 2), (60, 5)])
def test_claims_p42_production_budget_is_capped(monkeypatch, outer, seconds):
    assert js.TOTAL_SECONDS == 5.0 and js.FILE_SECONDS == 1.0
    monkeypatch.setattr(js, 'time', SimpleNamespace(monotonic=lambda: 100.0))
    calls = []
    def no_proof(claims, repositories, config, deadline):
        calls.append(deadline)
        raise js.NoProof
    monkeypatch.setattr(js, 'prove', no_proof)
    original = claim()
    before = asdict(original)
    js.rescue([original], SimpleNamespace(budget=SimpleNamespace(remaining=lambda: outer)), object())
    assert calls == ([] if seconds is None else [100+seconds])
    assert asdict(original) == before


@pytest.mark.parametrize('stage', ['startup', 'file', 'drain', 'cleanup', 'complete'])
def test_claims_p42_deadline_failure_discards_proof_and_closes_worker(tmp_path, monkeypatch, stage):
    now = [100.0]
    monkeypatch.setattr(js, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    fixed, oid = 'a'*40, 'b'*40
    text = 'export function moved_helper() {}'
    files = {'logic.py': ('c'*40, 1), 'other.mjs': (oid, len(text))}
    monkeypatch.setattr(js, 'configured', lambda *a: (Path('node'), Path('parser')))
    monkeypatch.setattr(js, 'tree', lambda *a: (fixed, files, 2))
    monkeypatch.setattr(js, 'candidates', lambda *a: {'moved_helper': {'other.mjs'}})
    monkeypatch.setattr(js, 'blobs', lambda *a: {oid: text})
    children = []

    class Worker:
        def __init__(self, *args, deadline, **kwargs):
            self.deadline, self.buffer, self.closed = deadline, b'', False
            self.process = SimpleNamespace(stdin=SimpleNamespace(close=lambda: None))
            self.lines = 0
            children.append(self)
        def line(self, deadline):
            self.lines += 1
            assert deadline == 101.0  # Production per-file/startup budget stays 1s.
            if stage == ('startup' if self.lines == 1 else 'file'):
                now[0] = 102.0
            js.remaining(deadline)
            if self.lines == 1:
                return dict(ready=True, version=js.VERSION, policy=js.POLICY)
            start = text.index('moved_helper')
            return dict(id=0, oid=oid, parsed=True, hits=[dict(name='moved_helper',
                line=1, start=start, end=start+len('moved_helper'), kind='FunctionDeclaration')])
        def send(self, data):
            pass
        def all(self):
            if stage == 'drain':
                now[0] = 106.0
            js.remaining(self.deadline)
            return 0, b''
        def close(self):
            self.closed = True
            if stage == 'cleanup':
                raise js.subprocess.TimeoutExpired('synthetic', 1)

    monkeypatch.setattr(js, 'Child', Worker)
    original = claim()
    before = asdict(original)
    repos = SimpleNamespace(roots=[tmp_path], commits={normalize_path(str(tmp_path)):fixed},
        aliases={}, budget=SimpleNamespace(remaining=lambda:60))
    js.rescue([original], repos, object())
    assert len(children) == 1 and children[0].closed
    assert children[0].deadline == 105.0
    if stage == 'complete':
        assert original.reason_code == 'found_outside_scope'
    else:
        assert asdict(original) == before


@pytest.mark.parametrize('buffered', [False, True])
def test_claims_p42_late_or_buffered_response_cannot_escape_deadline(monkeypatch, buffered):
    now = [102.0 if buffered else 100.0]
    monkeypatch.setattr(js, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    child = object.__new__(js.Child)
    child.failed, child.deadline = False, 105.0
    child.buffer = b'{}\n' if buffered else b''
    def get(timeout):
        assert timeout == 1.0
        now[0] = 102.0  # Simulate delayed scheduling after queue delivery.
        return b'{}\n'
    child.queue = SimpleNamespace(get=get)
    with pytest.raises(js.NoProof):
        child.line(101.0)


def test_claims_p42_expired_process_creation_reaps_child(monkeypatch):
    now, calls = [100.0], []
    monkeypatch.setattr(js, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    def popen(*args, **kwargs):
        now[0] = 106.0
        return object()
    monkeypatch.setattr(js.subprocess, 'Popen', popen)
    monkeypatch.setattr(js.Child, 'close', lambda self: calls.append('closed'))
    with pytest.raises(js.NoProof):
        js.Child(['synthetic'], deadline=105.0, limit=100)
    assert calls == ['closed']


def test_claims_p42_json_decode_cannot_return_after_frame_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(js, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    def decode(value):
        now[0] = 102.0
        return {}
    monkeypatch.setattr(js, 'json', SimpleNamespace(loads=decode))
    child = object.__new__(js.Child)
    child.deadline, child.buffer = 105.0, b'{}\n'
    with pytest.raises(js.NoProof):
        child.line(101.0)


def test_claims_p42_grep_is_single_threaded_and_keeps_match_protocol(monkeypatch, tmp_path):
    fixed = 'a'*40
    def fake_git(root, args, deadline, **kwargs):
        assert args[:2] == ['grep', '--threads=1']
        assert kwargs['data'] == b'moved_helper\n' and kwargs['codes'] == (0, 1)
        return (fixed+':other.mjs\x001\x00export function moved_helper() {}\n').encode()
    monkeypatch.setattr(js, 'git', fake_git)
    assert js.candidates(tmp_path, fixed, {'other.mjs':('b'*40, 32)},
                         ['moved_helper'], float('inf')) == {'moved_helper':{'other.mjs'}}


@pytest.mark.parametrize('fault', ['kill_race', 'wait_delay', 'persistent_delay'])
def test_claims_p42_cleanup_retries_are_bounded(fault):
    calls = []

    class Process:
        stdin = stdout = stderr = None

        def poll(self):
            return None

        def kill(self):
            calls.append('kill')
            if fault == 'kill_race':
                raise OSError('synthetic exit race')

        def wait(self, timeout):
            assert timeout == 1
            calls.append('wait')
            if fault == 'persistent_delay' or fault == 'wait_delay' and calls.count('wait') == 1:
                raise js.subprocess.TimeoutExpired('synthetic', timeout)
            return -1

    child = object.__new__(js.Child)
    child.process, child.threads = Process(), []
    if fault == 'persistent_delay':
        with pytest.raises(js.subprocess.TimeoutExpired):
            child.close()
    else:
        child.close()
    assert calls == ['kill', 'wait'] * (1 if fault == 'kill_race' else 2)
