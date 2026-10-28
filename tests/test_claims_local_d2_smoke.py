import io
import json
from pathlib import Path
import shutil
import argparse
import pytest
from urllib.error import HTTPError
from urllib.parse import urlsplit

from smtw.claims import local_smoke


FIXTURE = Path(__file__).parent / 'fixtures' / 'local-judge-d1'
MODEL = 'judge-qwen38-27b'
RAW_CANARY = 'RAW_ONLY_CANARY_7a3f_no_persistence'


class Reply:
    def __init__(self, value):
        self.data = json.dumps(value).encode('utf-8')
        self.status = 200
        self.closed = False

    def read(self, limit=-1):
        return self.data if limit < 0 else self.data[:limit]

    def close(self):
        self.closed = True


class SmokeOpener:
    def __init__(self, *, fail_health=False, fail_at_claim=None, response_fault=None):
        self.calls = []
        self.fail_health = fail_health
        self.fail_at_claim = fail_at_claim
        self.response_fault = response_fault
        self.answers = {row['id']: row['expected'] for row in json.loads(
            (FIXTURE / 'answers.json').read_text(encoding='utf-8'))['items']}

    def open(self, request, timeout):
        path = urlsplit(request.full_url).path
        body = json.loads(request.data) if request.data is not None else None
        self.calls.append((path, body))
        if path == '/health':
            if self.fail_health:
                raise HTTPError(request.full_url, 503, RAW_CANARY, {}, io.BytesIO(RAW_CANARY.encode()))
            return Reply({'status': 'ok'})
        if path == '/v1/models':
            return Reply({'data': [{'id': MODEL}]})
        if path == '/props':
            return Reply({'build_info': 'synthetic-build', 'n_ctx': 16384,
                          'model_path': 'synthetic-model.gguf'})
        if path == '/apply-template':
            return Reply({'prompt': '\n'.join(row['content'] for row in body['messages'])})
        if path == '/tokenize':
            return Reply({'tokens': [1] * 300})
        assert path == '/v1/chat/completions'
        packet = json.loads(body['messages'][1]['content'])
        item, = packet['data_block']
        if item['id'] == self.fail_at_claim:
            raise HTTPError(request.full_url, 503, RAW_CANARY, {}, io.BytesIO(RAW_CANARY.encode()))
        assert not {'branch', 'atom', 'fixed_commit', 'target_evidence', 'other_file_evidence',
                    'expected', 'truth', 'evidence_adequate'} & item.keys()
        row = dict(self.answers[item['id']])
        if self.response_fault == 'schema':
            row['unexpected'] = RAW_CANARY
        elif self.response_fault == 'ids':
            row['id'] = 'unexpected-id'
        return Reply({'model': MODEL, 'choices': [{'finish_reason': (
                      'length' if self.response_fault == 'length' else 'stop'),
                      'message': {'content': json.dumps({'judgments': [row]})}}],
                      'usage': {'prompt_tokens': 300, 'completion_tokens': 100, 'total_tokens': 400},
                      'discarded_wire_metadata': RAW_CANARY})


def argv(tmp_path, fixture=FIXTURE):
    return ['--provider', 'local', '--packets', str(fixture / 'packets'),
            '--scope-file', str(fixture / 'scope.json'), '--out-root', str(tmp_path / 'out'),
            '--max-calls', '12', '--seal', str(fixture / 'seal.json'),
            '--local-address', 'http://localhost:8099', '--local-model', MODEL,
            '--local-deadline-seconds', '180', '--local-input-tokens', '14000',
            '--local-output-tokens', '1024', '--local-json-mode', 'schema',
            '--local-protocol', 'openai-compatible', '--local-seed', '42',
            '--local-temperature', '0', '--local-reasoning', 'off',
            '--local-stop-file', str(tmp_path / 'STOP'),
            '--local-server-start-args-sha256', 'a' * 64]


def summaries(tmp_path):
    return list((tmp_path / 'out').rglob('smoke-summary.json'))


def assert_no_raw_body(tmp_path):
    for path in (tmp_path / 'out').rglob('*'):
        if path.is_file():
            assert RAW_CANARY.encode() not in path.read_bytes(), path


def test_twelve_sealed_items_use_fake_opener_and_keep_raw_success_body_out(tmp_path):
    opener = SmokeOpener()
    assert local_smoke.main(argv(tmp_path), opener=opener) == 0
    chats = [body for path, body in opener.calls if path == '/v1/chat/completions']
    assert len(chats) == 12
    summary = json.loads(summaries(tmp_path)[0].read_text(encoding='utf-8'))
    assert summary['expected_matches'] == summary['input_count'] == summary['evaluated_count'] == 12
    assert summary['not_evaluated_count'] == summary['local_error_count'] == 0
    assert summary['quote_not_in_excerpt'] == summary['missing_quote_or_ref'] == 0
    assert summary['all_item_ids'] == summary['completed_item_ids'] == local_smoke.IDS
    assert summary['seal_sha256'] == local_smoke.SEALED_D1_SHA256
    assert_no_raw_body(tmp_path)


def test_error_body_never_persists_and_remaining_smoke_items_are_not_evaluated(tmp_path):
    opener = SmokeOpener(fail_health=True)
    assert local_smoke.main(argv(tmp_path), opener=opener) == 2
    assert [path for path, _ in opener.calls] == ['/health']
    summary = json.loads(summaries(tmp_path)[0].read_text(encoding='utf-8'))
    assert summary['local_error_count'] == 1
    assert summary['not_evaluated_count'] == 12
    assert summary['not_evaluated_item_ids'] == local_smoke.IDS
    assert_no_raw_body(tmp_path)


def test_modified_sealed_input_is_refused_before_any_opener_call(tmp_path):
    fixture = tmp_path / 'fixture'
    shutil.copytree(FIXTURE, fixture)
    (fixture / 'packets' / 'packet-001.json').write_text('{}', encoding='utf-8')
    opener = SmokeOpener()
    assert local_smoke.main(argv(tmp_path, fixture), opener=opener) == 2
    assert opener.calls == []
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('fault,kind', [('length', 'finish_reason_length'),
                                      ('schema', 'schema_violation'), ('ids', 'id_mismatch')])
def test_chat_failure_preserves_preflight_receipt_without_raw_body(tmp_path, fault, kind):
    opener = SmokeOpener(response_fault=fault)
    assert local_smoke.main(argv(tmp_path), opener=opener) == 2
    assert sum(path == '/v1/chat/completions' for path, _ in opener.calls) == 1
    call = json.loads((tmp_path / 'out' / 'calls.jsonl').read_text(encoding='utf-8'))
    assert call['kind'] == kind
    receipt = call['transport_receipt']
    assert receipt['identity']['model_id'] == MODEL
    assert receipt['identity']['build_info'] == 'synthetic-build'
    assert receipt['input_tokens'] == 300
    assert receipt['input_token_method'] == 'apply_template'
    assert receipt['response_schema_sha256'] == call['request_schema_sha256']
    assert receipt['server_start_args_sha256'] == 'a' * 64
    assert receipt['request']['max_tokens'] == 1024
    assert_no_raw_body(tmp_path)


def test_existing_stop_file_stops_all_twelve_without_any_opener_call(tmp_path):
    (tmp_path / 'STOP').write_text('stop', encoding='utf-8')
    opener = SmokeOpener()
    assert local_smoke.main(argv(tmp_path), opener=opener) == 2
    assert opener.calls == []
    summary = json.loads(summaries(tmp_path)[0].read_text(encoding='utf-8'))
    assert summary['not_evaluated_count'] == 12
    assert summary['remaining_item_ids'] == local_smoke.IDS


def test_second_item_error_preserves_first_and_never_sends_third(tmp_path):
    opener = SmokeOpener(fail_at_claim='d1-02')
    assert local_smoke.main(argv(tmp_path), opener=opener) == 2
    chats = [body for path, body in opener.calls if path == '/v1/chat/completions']
    assert [json.loads(body['messages'][1]['content'])['data_block'][0]['id']
            for body in chats] == ['d1-01', 'd1-02']
    summary = json.loads(summaries(tmp_path)[0].read_text(encoding='utf-8'))
    assert summary['completed_item_ids'] == ['d1-01']
    assert summary['remaining_item_ids'] == local_smoke.IDS[1:]
    run_summary = json.loads(next((tmp_path / 'out').rglob('run-summary.json')).read_text(encoding='utf-8'))
    assert run_summary['all_item_ids'] == local_smoke.IDS
    assert run_summary['completed_item_ids'] == ['d1-01']
    assert run_summary['remaining_item_ids'] == local_smoke.IDS[1:]
    assert_no_raw_body(tmp_path)


def test_claims_judge_dispatch_preserves_subscription_argument_error():
    from smtw.claims.cli import add_claims_parser
    parser = argparse.ArgumentParser()
    add_claims_parser(parser.add_subparsers(dest='command', required=True))
    args = parser.parse_args(['claims', 'judge', '--packets', 'synthetic',
                              '--scope-file', 'synthetic', '--out-root', 'synthetic'])
    with pytest.raises(SystemExit) as error:
        args.func(args)
    assert error.value.code == 2


def test_real_smoke_path_constructs_injected_test_opener_and_has_no_hidden_default(tmp_path, monkeypatch):
    from smtw.claims import local_http
    opener = SmokeOpener()
    monkeypatch.setattr(local_http, 'build_direct_opener', lambda: opener)
    assert local_smoke.main(argv(tmp_path)) == 0
    assert sum(path == '/v1/chat/completions' for path, _ in opener.calls) == 12
    assert_no_raw_body(tmp_path)
