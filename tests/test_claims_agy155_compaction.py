"""agy 1.5.5 write tools, chunked transcripts and compaction audit.

Every transcript, path and report here is synthetic (``C:/sample/...`` or
``tmp_path``); no recorded session is used.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from smtw.claims.check import run
from smtw.claims.cli import render_text
from smtw.claims.model import Budget
from smtw.claims.packet_reads import parse_packet_result
from smtw.claims.paths import normalize_path
from smtw.claims.transcripts import (Call, command_write_targets, discover_agy, parse_result,
                                     read_transcript, resolve_agy)
from tests.claims_support import agy_call, generic, make_case, view, write_transcript

ENGINE = 'C:/sample/engine.py'
OTHER = 'C:/sample/other.py'


def checkpoint(step=None):
    row = {'type': 'CHECKPOINT', 'source': 'SYSTEM', 'status': 'DONE',
           'content': 'synthetic compaction summary'}
    if step is not None:
        row['step_index'] = step
    return row


def codex_call(name, args, ident):
    return {'type': 'response_item', 'timestamp': '2026-01-01T00:00:00Z',
            'payload': {'type': 'function_call', 'name': name, 'call_id': ident,
                        'arguments': json.dumps(args)}}


def codex_output(text, ident):
    return {'type': 'response_item', 'payload': {
        'type': 'function_call_output', 'call_id': ident,
        'output': json.dumps({'output': text, 'exit_code': 0})}}


def transcript_of(tmp_path, report, rows):
    t = write_transcript(tmp_path / 'transcript_slice.jsonl', rows)
    return read_transcript(t, 'agy', report, report.read_text(encoding='utf-8'), {}, Budget(30))


def verdicts(result):
    return [(c['check_type'], c['verdict'], c['reason_code']) for c in result['claims']]


# 1) write_file / edit_file target binding ---------------------------------

def test_read_file_path_is_never_a_binding_argument():
    # read_file output carries no ``File Path:`` header; Path must not bind it.
    assert Call('read_file', {'Path': ENGINE}, 1, '').path == ''
    assert Call('read_file', {'Path': ENGINE}, 1, '').write_path == ''
    e, = parse_result(Call('read_file', {'Path': ENGINE}, 1, ''),
                      'Showing lines 1-1 of 1\n1: def invented_name(): pass',
                      'synthetic', Path('.'), Budget(10))
    assert e.path == ''


def test_read_file_path_read_does_not_prove_a_file_claim(tmp_path):
    r, t = make_case(tmp_path, '`engine.py:1` “def valid_name(): pass”', [
        agy_call('read_file', Path=ENGINE),
        generic('Showing lines 1-1 of 1\n1: def valid_name(): pass')])
    result = run(r, agy=str(t))
    assert result['claims'][0]['verdict'] != 'match'
    assert result['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('name,args', [
    ('write_file', {'Path': ENGINE, 'Content': 'def invented_name(): pass\n'}),
    ('edit_file', {'Path': ENGINE, 'Target': 'old', 'Replacement': 'def invented_name(): pass'}),
])
def test_agy155_write_targets_are_path_bound(name, args):
    call = Call(name, args, 3, '')
    assert call.write and not call.read
    assert call.write_path == ENGINE
    assert call.path == ''
    assert command_write_targets(call) == ([ENGINE], False)


@pytest.mark.parametrize('name,args', [
    ('write_file', {'Path': ENGINE, 'Content': 'def invented_name(): pass\n'}),
    ('edit_file', {'Path': ENGINE, 'Target': 'old', 'Replacement': 'def invented_name(): pass'}),
])
def test_self_write_then_view_is_not_evidence(tmp_path, name, args):
    rows = [agy_call(name, **args), generic('ok'),
            agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def invented_name(): pass')]
    r, t = make_case(tmp_path, '`engine.py` 함수 `invented_name`', rows)
    assert run(r, agy=str(t))['claims'][0]['verdict'] == 'unverifiable'
    assert transcript_of(tmp_path, r, rows).evidence == []


def test_write_file_exclusion_is_limited_to_its_own_target(tmp_path):
    rows = [agy_call('write_file', Path=OTHER, Content='x = 1\n'), generic('ok'),
            agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass')]
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`', rows)
    assert run(r, agy=str(t))['claims'][0]['verdict'] == 'match'


def test_read_before_self_write_stays_evidence(tmp_path):
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            agy_call('edit_file', Path=ENGINE, Target='valid_name', Replacement='renamed'), generic('ok')]
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`', rows)
    assert run(r, agy=str(t))['claims'][0]['verdict'] == 'match'


def test_write_file_to_report_path_sets_after_report_cutoff(tmp_path):
    r, _ = make_case(tmp_path, '`engine.py` 함수 `valid_name`')
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            agy_call('write_file', Path=str(r), Content=r.read_text(encoding='utf-8')), generic('ok'),
            agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass')]
    assert [e.after_report for e in transcript_of(tmp_path, r, rows).evidence] == [False, True]


def test_write_file_content_equal_to_report_marks_report_copy(tmp_path):
    copy = 'C:/sample/report-copy.md'
    r, _ = make_case(tmp_path, '`engine.py` 함수 `valid_name`')
    rows = [agy_call('write_file', Path=copy, Content=r.read_text(encoding='utf-8')), generic('ok'),
            agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            agy_call('view_file', AbsolutePath=copy), view(copy, '`engine.py` 함수 `valid_name`')]
    evidence = transcript_of(tmp_path, r, rows).evidence
    assert [(normalize_path(e.path), e.after_report) for e in evidence] == [(ENGINE.casefold(), True)]


def test_edit_file_to_report_path_sets_cutoff(tmp_path):
    r, _ = make_case(tmp_path, '`engine.py` 함수 `valid_name`')
    rows = [agy_call('edit_file', Path=str(r), Target='a', Replacement='b'), generic('ok'),
            agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass')]
    assert [e.after_report for e in transcript_of(tmp_path, r, rows).evidence] == [True]


# --agy-auto discovery --------------------------------------------------------

def brain_case(tmp_path, text='2건 중 1건'):
    r, _ = make_case(tmp_path, text)
    return r, tmp_path / 'fake-brain'


@pytest.mark.parametrize('tools,status', [
    ([('write_file', 'exact')], 'PASS'),
    ([('write_file', 'changed')], 'INCOMPLETE'),
    ([('write_file', 'exact'), ('edit_file', None)], 'INCOMPLETE'),
    ([('edit_file', None)], 'INCOMPLETE'),
    ([('write_file', 'append')], 'INCOMPLETE'),
])
def test_agy_auto_recognizes_write_file_and_edit_file(tmp_path, tools, status):
    r, brain = brain_case(tmp_path)
    rows = []
    for name, mode in tools:
        if name == 'write_file':
            args = {'Path': str(r), 'Content': 'changed' if mode == 'changed' else r.read_text(encoding='utf-8')}
            if mode == 'append':
                args['Append'] = True
        else:
            args = {'Path': str(r), 'Target': '1건', 'Replacement': '1건'}
        rows += [agy_call(name, **args), generic('ok')]
    write_transcript(brain / 'synthetic-a/.system_generated/logs/transcript_full.jsonl', rows)
    assert run(r, agy_auto=True, brain_root=brain)['status'] == status


def test_agy_auto_scans_chunks_only_without_full_transcript(tmp_path):
    r, brain = brain_case(tmp_path)
    exact = [agy_call('write_file', Path=str(r), Content=r.read_text(encoding='utf-8')), generic('ok')]
    chunk = brain / 'synthetic-a/.system_generated/logs/chunks/transcript_full'
    write_transcript(chunk / '00000000.jsonl', exact[:1])
    write_transcript(chunk / '00000001.jsonl', exact[1:])
    found = discover_agy(r, r.read_text(encoding='utf-8'), brain, {}, Budget(30))
    assert found == [chunk]
    assert run(r, agy_auto=True, brain_root=brain)['status'] == 'PASS'
    # A full transcript beside the chunks is the only candidate for that conversation.
    write_transcript(chunk.parent.parent / 'transcript_full.jsonl', exact)
    assert discover_agy(r, r.read_text(encoding='utf-8'), brain, {}, Budget(30)) == [
        chunk.parent.parent / 'transcript_full.jsonl']


# chunked transcript fallback -----------------------------------------------

def test_resolve_agy_chunk_fallback_keeps_existing_results(tmp_path):
    logs = tmp_path / 'synthetic-id/.system_generated/logs'
    full = logs / 'transcript_full.jsonl'
    assert resolve_agy('synthetic-id', tmp_path) == full
    chunks = logs / 'chunks/transcript_full'
    chunks.mkdir(parents=True)
    assert resolve_agy('synthetic-id', tmp_path) == full  # empty chunk dir is no fallback
    write_transcript(chunks / '00000000.jsonl', [checkpoint()])
    assert resolve_agy('synthetic-id', tmp_path) == chunks
    assert resolve_agy(str(chunks), tmp_path) == chunks
    write_transcript(full, [checkpoint()])
    assert resolve_agy('synthetic-id', tmp_path) == full
    assert resolve_agy(str(tmp_path / 'x.jsonl'), tmp_path) == tmp_path / 'x.jsonl'


def test_chunked_transcript_reads_in_order_with_aux_root(tmp_path):
    system = tmp_path / 'brain/synthetic-id/.system_generated'
    aux = system / 'steps/7/output.txt'
    aux.parent.mkdir(parents=True)
    aux.write_text('def chunk_name(): pass\n', encoding='utf-8')
    chunks = system / 'logs/chunks/transcript_full'
    write_transcript(chunks / '00000000.jsonl', [
        agy_call('view_file', AbsolutePath=ENGINE)])
    write_transcript(chunks / '00000001.jsonl', [
        view(ENGINE, 'def valid_name(): pass'),
        agy_call('run_command', CommandLine=f'cat {OTHER}')])
    write_transcript(chunks / '00000002.jsonl', [
        generic('Output has been saved to: C:/fake/steps/7/output.txt')])
    r, _ = make_case(tmp_path, '`engine.py` 함수 `valid_name`\n`other.py` 함수 `chunk_name`')
    transcript = read_transcript(chunks, 'agy', r, r.read_text(encoding='utf-8'), {}, Budget(30))
    assert transcript.valid
    assert [e.order for e in transcript.evidence] == [1, 3]
    assert transcript.evidence[1].aux_path == str(aux.resolve())
    result = run(r, agy='synthetic-id', brain_root=tmp_path / 'brain')
    assert result['status'] == 'PASS', verdicts(result)


# 2) compaction audit -------------------------------------------------------

def sliced_chunks(directory, rows, size=7, padded=False):
    """Mimic agy's fixed-size byte slices that cut records and UTF-8 sequences."""
    data = ('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n').encode('utf-8')
    directory.mkdir(parents=True, exist_ok=True)
    pieces = [data[i:i + size] for i in range(0, len(data), size)]
    for index, piece in enumerate(pieces):
        (directory / (f'{index:08}.jsonl' if padded else f'{index}.jsonl')).write_bytes(piece)
    broken = 0
    for piece in pieces:
        try:
            piece.decode('utf-8')
        except UnicodeDecodeError:
            broken += 1
    assert len(pieces) > 10 and broken  # boundaries really cut records and characters
    return directory


def test_byte_sliced_chunks_read_as_one_stream_in_numeric_order(tmp_path):
    korean = 'def valid_name(): pass  # 한글 주석으로 UTF-8 경계를 만든다'
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, korean)]
    chunks = sliced_chunks(tmp_path / 'brain/synthetic-id/.system_generated/logs/chunks/transcript_full', rows)
    r, _ = make_case(tmp_path, '`engine.py` 함수 `valid_name`')
    transcript = read_transcript(chunks, 'agy', r, r.read_text(encoding='utf-8'), {}, Budget(30))
    assert transcript.valid and not transcript.warnings
    assert [e.text for e in transcript.evidence] == [korean]
    assert resolve_agy('synthetic-id', tmp_path / 'brain') == chunks
    assert run(r, agy='synthetic-id', brain_root=tmp_path / 'brain')['status'] == 'PASS'


def test_agy_auto_finds_byte_sliced_chunked_conversation(tmp_path):
    r, brain = brain_case(tmp_path, '보고서 본문 2건 중 1건')
    rows = [agy_call('write_file', Path=str(r), Content=r.read_text(encoding='utf-8')), generic('ok')]
    chunks = sliced_chunks(brain / 'synthetic-a/.system_generated/logs/chunks/transcript_full', rows, padded=True)
    assert discover_agy(r, r.read_text(encoding='utf-8'), brain, {}, Budget(30)) == [chunks]
    assert run(r, agy_auto=True, brain_root=brain)['status'] == 'PASS'


def test_checkpoint_after_report_write_is_not_counted(tmp_path):
    r, _ = make_case(tmp_path, '`engine.py` 함수 `valid_name`')
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            agy_call('write_file', Path=str(r), Content=r.read_text(encoding='utf-8')), generic('ok'),
            checkpoint(step=9)]
    t = write_transcript(tmp_path / 'transcript_slice.jsonl', rows)
    result = run(r, agy=str(t))
    assert result['status'] == 'PASS'
    assert result['compaction'] == {'count': 0, 'orders': [], 'steps': [],
                                    'stale_claim_lines': [], 'stale_files': []}
    write_transcript(t, rows[:2] + [checkpoint(step=1)] + rows[2:])
    assert run(r, agy=str(t))['compaction'] == {'count': 1, 'orders': [3], 'steps': [1],
                                                'stale_claim_lines': [1], 'stale_files': [ENGINE.casefold()]}


def test_reread_after_report_write_does_not_clear_stale(tmp_path):
    r, _ = make_case(tmp_path, '`engine.py` 함수 `valid_name`')
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            checkpoint(),
            agy_call('write_file', Path=str(r), Content=r.read_text(encoding='utf-8')), generic('ok'),
            agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            agy_call('read_file', Path=ENGINE), generic('Showing lines 1-1 of 1\n1: def valid_name(): pass')]
    t = write_transcript(tmp_path / 'transcript_slice.jsonl', rows)
    assert run(r, agy=str(t))['compaction']['stale_claim_lines'] == [1]


def test_failed_pointer_reread_does_not_clear_stale(tmp_path):
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            checkpoint(),
            agy_call('run_command', CommandLine=f'cat {ENGINE}'),
            generic('Output has been saved to: C:/fake/steps/9/output.txt')]
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`', rows)
    transcript = read_transcript(t, 'agy', r, r.read_text(encoding='utf-8'), {}, Budget(30))
    assert transcript.evidence[-1].problem_code == 'pointer_missing'
    assert run(r, agy=str(t))['compaction']['stale_claim_lines'] == [1]


def test_successful_read_file_batch_counts_as_audit_reread_only(tmp_path):
    base = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'), checkpoint()]
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`', base)
    stale = run(r, agy=str(t))
    write_transcript(t, base + [agy_call('read_file', Path=ENGINE),
                                generic('Showing lines 1-1 of 1\n1: def valid_name(): pass')])
    hinted = run(r, agy=str(t))
    assert stale['compaction']['stale_claim_lines'] == [1]
    assert hinted['compaction']['stale_claim_lines'] == []
    # The hint is audit metadata: evidence, verdicts and warnings stay identical.
    for key in ('status', 'claims', 'warnings', 'summary', 'verdict_reason_counts'):
        assert hinted[key] == stale[key], key
    transcript = read_transcript(t, 'agy', r, r.read_text(encoding='utf-8'), {}, Budget(30))
    assert all(e.order != 4 or not e.path for e in transcript.evidence)
    assert transcript.compaction_rereads == [(1, ENGINE.casefold()), (4, ENGINE.casefold())]


@pytest.mark.parametrize('tail', [
    [agy_call('read_file', Path=ENGINE), generic('Error: file not found')],
    [agy_call('read_file', Path=ENGINE), generic('1: def valid_name(): pass', status='ERROR')],
    [{'type': 'PLANNER_RESPONSE', 'tool_calls': [
        {'name': 'read_file', 'args': {'Path': ENGINE}},
        {'name': 'read_file', 'args': {'Path': OTHER}}]},
     generic('1: def valid_name(): pass'), generic('missing', status='FAILED')],
    [agy_call('edit_file', Path=ENGINE, Target='a', Replacement='b'), generic('ok'),
     agy_call('read_file', Path=ENGINE), generic('1: def valid_name(): pass')],
    [{'type': 'PLANNER_RESPONSE', 'tool_calls': [{'name': 'read_file', 'args': {'Path': ENGINE}}]}],
], ids=['error-text', 'error-status', 'batch-one-failed', 'self-written', 'unanswered'])
def test_read_file_hint_requires_fully_successful_unwritten_batch(tmp_path, tail):
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            checkpoint()] + tail
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`', rows)
    assert run(r, agy=str(t))['compaction']['stale_claim_lines'] == [1]


def test_checkpoint_without_reread_lists_stale_claim(tmp_path):
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            checkpoint(step=41)]
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`', rows)
    result = run(r, agy=str(t))
    assert result['status'] == 'PASS'
    assert result['compaction'] == {'count': 1, 'orders': [3], 'steps': [41],
                                    'stale_claim_lines': [1], 'stale_files': [ENGINE.casefold()]}


def test_reread_after_checkpoint_clears_stale_claim(tmp_path):
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            checkpoint(),
            agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass')]
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`', rows)
    result = run(r, agy=str(t))
    assert result['compaction'] == {'count': 1, 'orders': [3], 'steps': [3],
                                    'stale_claim_lines': [], 'stale_files': []}


def test_only_last_checkpoint_counts_and_lines_are_unique_sorted(tmp_path):
    rows = [agy_call('view_file', AbsolutePath=OTHER), view(OTHER, 'def other_name(): pass'),
            checkpoint(step=5),
            agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass\ndef second_name(): pass'),
            checkpoint(step=9)]
    text = '`engine.py` 함수 `valid_name`, `second_name`\n`other.py` 함수 `other_name`'
    r, t = make_case(tmp_path, text, rows)
    result = run(r, agy=str(t))
    assert result['compaction']['orders'] == [3, 6]
    assert result['compaction']['steps'] == [5, 9]
    assert result['compaction']['stale_claim_lines'] == [1, 2]
    assert result['compaction']['stale_files'] == sorted([ENGINE.casefold(), OTHER.casefold()])


def test_checkpoint_never_changes_verdicts_or_warnings(tmp_path):
    text = '`engine.py` 함수 `valid_name`, `missing_name`\n2건 중 1건'
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass')]
    r, t = make_case(tmp_path, text, rows)
    plain = run(r, agy=str(t))
    write_transcript(t, rows + [checkpoint(step=1)])
    audited = run(r, agy=str(t))
    for key in ('status', 'claims', 'warnings', 'summary', 'verdict_reason_counts', 'transcript_format'):
        assert audited[key] == plain[key], key
    assert plain['compaction']['count'] == 0 and audited['compaction']['count'] == 1


def test_no_checkpoint_reports_empty_audit_and_no_text_line(tmp_path):
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass')]
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`', rows)
    result = run(r, agy=str(t))
    assert result['compaction'] == {'count': 0, 'orders': [], 'steps': [],
                                    'stale_claim_lines': [], 'stale_files': []}
    assert '컨텍스트 압축' not in render_text(result)


def test_render_text_summarizes_compaction(tmp_path):
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            checkpoint(step=2)]
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`', rows)
    text = render_text(run(r, agy=str(t)))
    assert text.splitlines()[-1] == '컨텍스트 압축 감지: 1회 (압축 후 미재열람 인용 1건)'


def test_non_integer_step_index_falls_back_to_row_order(tmp_path):
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            checkpoint(step='7'), checkpoint(step=True)]
    r, _ = make_case(tmp_path, '`engine.py` 함수 `valid_name`')
    transcript = transcript_of(tmp_path, r, rows)
    assert transcript.compaction_orders == [3, 4]
    assert transcript.compaction_steps == [3, 4]


def git(root, *args):
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=synthetic', '-c', 'user.email=synthetic@example.invalid',
                    '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True)


def test_fixed_commit_and_source_file_are_exempt(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'fixed.py').write_text('def fixed_name(): pass\n', encoding='utf-8')
    git(repo, 'init', '-q')
    git(repo, 'add', 'fixed.py')
    git(repo, 'commit', '-q', '-m', 'synthetic')
    sha = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True,
                         text=True, check=True).stdout.strip()
    snapshot = tmp_path / 'snapshot.py'
    snapshot.write_text('def snapshot_name(): pass\n', encoding='utf-8')
    rows = [agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
            checkpoint(step=3)]
    text = ('`engine.py` 함수 `valid_name`\n`fixed.py` 함수 `fixed_name`\n'
            '`snapshot.py` 함수 `snapshot_name`')
    r, t = make_case(tmp_path, text, rows)
    result = run(r, agy=str(t), commits={str(repo): sha}, sources={'snapshot.py': str(snapshot)})
    assert [c['verdict'] for c in result['claims']] == ['match', 'match', 'match']
    assert result['compaction']['stale_claim_lines'] == [1]
    assert result['compaction']['stale_files'] == [ENGINE.casefold()]


def test_codex_compacted_row_is_recorded(tmp_path):
    rows = [codex_call('exec_command', {'cmd': f'Get-Content {ENGINE}'}, 'read-1'),
            codex_output('def valid_name(): pass\n', 'read-1'),
            {'type': 'compacted', 'timestamp': '2026-01-01T00:01:00Z', 'payload': {'message': 'synthetic'}}]
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`', rows)
    result = run(r, codex=t)
    assert result['status'] == 'PASS'
    assert result['compaction'] == {'count': 1, 'orders': [3], 'steps': [3],
                                    'stale_claim_lines': [1], 'stale_files': [ENGINE.casefold()]}
    write_transcript(t, rows + [codex_call('exec_command', {'cmd': f'Get-Content {ENGINE}'}, 'read-2'),
                                codex_output('def valid_name(): pass\n', 'read-2')])
    assert run(r, codex=t)['compaction']['stale_claim_lines'] == []


def test_codex_compacted_alone_is_not_a_tool_call(tmp_path):
    r, t = make_case(tmp_path, '2건 중 1건', [{'type': 'compacted', 'payload': {}}])
    transcript = read_transcript(t, 'codex', r, r.read_text(encoding='utf-8'), {}, Budget(30))
    assert transcript.compaction_orders == [1] and not transcript.valid


# 3) packet hard-exclusion path -------------------------------------------

def test_excluded_literal_keeps_resolved_excluded_parent(tmp_path):
    call = Call('run_command', {'CommandLine': 'cat customer-data/list.md', 'Cwd': 'C:/sample/work'}, 1, '')
    e, = parse_packet_result(call, 'EXCLUDED_MARKER', 'synthetic', tmp_path, Budget(10))
    assert e.control_only and not e.text and not e.lines
    assert e.path == 'c:/sample/work/customer-data/list.md'


def test_excluded_literal_absolute_and_ordinary_paths_unchanged(tmp_path):
    absolute = Call('run_command', {'CommandLine': 'cat C:/sample/customer-data/list.md'}, 1, '')
    e, = parse_packet_result(absolute, 'EXCLUDED_MARKER', 'synthetic', tmp_path, Budget(10))
    assert e.path == 'C:/sample/customer-data/list.md'
    ordinary = Call('run_command', {'CommandLine': 'cat card.md', 'Cwd': 'C:/sample/work'}, 1, '')
    e, = parse_packet_result(ordinary, 'APPROVED_MARKER', 'synthetic', tmp_path, Budget(10))
    assert e.path == '' and e.control_only


def test_compaction_reread_matches_relative_and_absolute_paths(tmp_path):
    rows = [agy_call('run_command', CommandLine='Get-Content sub/engine.py'),
            generic('def valid_name(): pass\n'),
            checkpoint(step=3),
            agy_call('read_file', Path='C:/sample/repo/sub/engine.py'),
            generic('Showing lines 1-1 of 1\n1: def valid_name(): pass')]
    r, t = make_case(tmp_path, '`sub/engine.py` 함수 `valid_name`', rows)
    result = run(r, agy=str(t))
    assert result['status'] == 'PASS'
    assert result['compaction']['stale_claim_lines'] == []
    assert result['compaction']['stale_files'] == []


@pytest.mark.parametrize('mode', [
    'relative_turn_cwd',
    'absolute_add',
    'absolute_update',
    'js_writes_absolute',
    'js_writes_relative_turn_cwd',
])
def test_codex_apply_patch_report_write_sets_cutoff_for_reads_and_compaction(tmp_path, mode):
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`')
    rel_patch = '*** Begin Patch\n*** Add File: report.md\n+`engine.py` 함수 `valid_name`\n*** End Patch'
    abs_add = f'*** Begin Patch\n*** Add File: {r.as_posix()}\n+`engine.py` 함수 `valid_name`\n*** End Patch'
    abs_update = f'*** Begin Patch\n*** Update File: {r.as_posix()}\n@@\n+`engine.py` 함수 `valid_name`\n*** End Patch'
    rows = []
    if mode == 'relative_turn_cwd':
        rows.append({'type': 'turn_context', 'payload': {'cwd': str(tmp_path)}})
        rows.append(codex_call('apply_patch', {'input': rel_patch}, 'patch-report'))
        rows.append(codex_output('Done', 'patch-report'))
    elif mode == 'absolute_add':
        rows.append({'type': 'response_item', 'payload': {
            'type': 'custom_tool_call', 'name': 'apply_patch',
            'call_id': 'patch-report', 'input': abs_add}})
        rows.append({'type': 'response_item', 'payload': {
            'type': 'custom_tool_call_output', 'call_id': 'patch-report', 'output': 'Done'}})
    elif mode == 'absolute_update':
        rows.append(codex_call('apply_patch', {'patch': abs_update}, 'patch-report'))
        rows.append(codex_output('Done', 'patch-report'))
    elif mode == 'js_writes_absolute':
        rows.append({'type': 'response_item', 'payload': {
            'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'patch-report',
            'input': f'text(await tools.apply_patch({json.dumps(abs_add)}));'}})
        rows.append({'type': 'response_item', 'payload': {
            'type': 'custom_tool_call_output', 'call_id': 'patch-report', 'output': 'Done'}})
    else:
        rows.append({'type': 'turn_context', 'payload': {'cwd': str(tmp_path)}})
        rows.append({'type': 'response_item', 'payload': {
            'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'patch-report',
            'input': f'text(await tools.apply_patch({json.dumps(rel_patch)}));'}})
        rows.append({'type': 'response_item', 'payload': {
            'type': 'custom_tool_call_output', 'call_id': 'patch-report', 'output': 'Done'}})
    rows += [
        codex_call('exec_command', {'cmd': f'Get-Content {ENGINE}'}, 'read-after'),
        codex_output('def valid_name(): pass\n', 'read-after'),
        {'type': 'compacted', 'timestamp': '2026-01-01T00:01:00Z', 'payload': {'message': 'synthetic'}},
    ]
    write_transcript(t, rows)
    transcript = read_transcript(t, 'codex', r, r.read_text(encoding='utf-8'), {}, Budget(30))
    assert [e.after_report for e in transcript.evidence] == [True]
    assert transcript.compaction_orders == []
    result = run(r, codex=t)
    assert result['status'] == 'INCOMPLETE'
    assert result['claims'][0]['verdict'] == 'unverifiable'
    assert result['compaction'] == {'count': 0, 'orders': [], 'steps': [],
                                    'stale_claim_lines': [], 'stale_files': []}


@pytest.mark.parametrize('mode', [
    'redirect_absolute',
    'redirect_relative_cwd',
    'set_content_absolute',
    'set_content_relative_cwd',
])
def test_agy_shell_write_to_report_sets_cutoff_for_reads_and_checkpoint(tmp_path, mode):
    r, t = make_case(tmp_path, '`engine.py` 함수 `valid_name`')
    if mode == 'redirect_absolute':
        write_row = agy_call('run_command', CommandLine=f'echo text > "{r.as_posix()}"')
    elif mode == 'redirect_relative_cwd':
        write_row = agy_call('run_command', CommandLine='echo text > report.md', Cwd=str(tmp_path))
    elif mode == 'set_content_absolute':
        write_row = agy_call('run_command', CommandLine=f'Set-Content "{r.as_posix()}" text')
    else:
        write_row = agy_call('run_command', CommandLine='Set-Content report.md text', Cwd=str(tmp_path))
    rows = [
        write_row, generic(''),
        agy_call('view_file', AbsolutePath=ENGINE), view(ENGINE, 'def valid_name(): pass'),
        checkpoint(step=12),
    ]
    write_transcript(t, rows)
    transcript = read_transcript(t, 'agy', r, r.read_text(encoding='utf-8'), {}, Budget(30))
    assert [e.after_report for e in transcript.evidence] == [True]
    assert transcript.compaction_orders == []
    result = run(r, agy=str(t))
    assert result['status'] == 'INCOMPLETE'
    assert result['claims'][0]['verdict'] == 'unverifiable'
    assert result['compaction'] == {'count': 0, 'orders': [], 'steps': [],
                                    'stale_claim_lines': [], 'stale_files': []}
