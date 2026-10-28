import io
import json
from pathlib import Path
import threading
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler

import pytest

import smtw.claims.local_http as local_http
from smtw.claims.local_http import (
    LocalHttpError,
    LocalHttpGate,
    LocalHttpTransport,
    build_direct_opener,
    validate_loopback_address,
)


MODEL = "judge-qwen38-27b"


class FakeResponse:
    def __init__(self, value, status=200):
        self.data = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
        self.status = status
        self.closed = False

    def getcode(self):
        return self.status

    def read(self, _limit=-1):
        return self.data

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, routes):
        self.routes = {path: list(values) for path, values in routes.items()}
        self.calls = []

    def open(self, request, timeout):
        path = urlsplit(request.full_url).path
        body = json.loads(request.data) if request.data is not None else None
        self.calls.append({"url": request.full_url, "path": path,
                           "method": request.get_method(), "body": body, "timeout": timeout})
        action = self.routes[path].pop(0)
        if isinstance(action, BaseException):
            raise action
        if callable(action):
            action = action(request, timeout)
        return action


def schema():
    return {"$schema": "discard", "title": "discard", "type": "object",
            "properties": {"judgments": {"type": "array", "items": {
                "title": "discard", "type": "object", "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["supported", "contradicted",
                                                                  "not_enough_evidence"]},
                    "rationale": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "evidence_ref": {"type": "integer"}},
                "required": ["id", "verdict", "rationale"]}}},
            "required": ["judgments"], "additionalProperties": False}


def request(**updates):
    value = {"protocol": "openai-compatible", "address": "http://localhost:8099",
             "model": MODEL, "deadline_seconds": 180, "input_tokens": 14_000,
             "output_tokens": 1_024, "json_mode": "schema", "seed": 42,
             "temperature": 0, "reasoning": "off", "response_schema": schema(),
             "packet_sha256": "a" * 64, "request_id": "request-1",
             "claim_ids": ["claim-1"], "instructions": "Return judgments.",
             "untrusted_data": {"data_block": [{"id": "claim-1", "text": "toy"}]}}
    value.update(updates)
    return value


def chat(*, model=MODEL, finish="stop", content=None, usage=None):
    content = content if content is not None else json.dumps({"judgments": [{
        "id": "claim-1", "rationale": "Toy evidence.", "verdict": "supported",
        "evidence_ref": 1, "evidence_quote": "toy"}]})
    return {"model": model, "choices": [{"finish_reason": finish,
                                           "message": {"content": content}}],
            "usage": usage or {"prompt_tokens": 9, "completion_tokens": 5, "total_tokens": 14}}


def routes(*, token_count=4, chat_value=None, apply=None):
    return {"/health": [FakeResponse({"status": "ok"})],
            "/v1/models": [FakeResponse({"data": [{"id": MODEL}]})],
            "/props": [FakeResponse({"build_info": "build-1", "n_ctx": 16_384,
                                      "model_path": "model.gguf", "chat_template": "template {{ messages }}"})],
            "/apply-template": [apply or FakeResponse({"prompt": "rendered prompt"})],
            "/tokenize": [FakeResponse({"tokens": list(range(token_count))})],
            "/v1/chat/completions": [FakeResponse(chat_value or chat())]}


def transport(opener, **kwargs):
    return LocalHttpTransport(opener=opener, gate=kwargs.pop("gate", LocalHttpGate()),
                              wait_interval=0.001, **kwargs)


def assert_safe_error(error, *, stage, kind, status=None):
    assert error.record == {"request_id": "request-1", "claim_ids": ["claim-1"],
                            "stage": stage, "kind": kind, "http_status": status,
                            "elapsed_ms": error.record["elapsed_ms"], "not_evaluated": True,
                            "server_computation_ended": error.record["server_computation_ended"]}
    encoded = json.dumps(error.record)
    assert "toy" not in encoded and "secret" not in encoded and "exception" not in encoded


def test_success_preflights_counts_and_posts_strict_openai_request():
    opener = FakeOpener(routes())
    start_args_sha = "b" * 64
    sent = request(server_start_args_sha256=start_args_sha)
    result = transport(opener)(sent)

    assert [call["path"] for call in opener.calls] == [
        "/health", "/v1/models", "/props", "/apply-template", "/tokenize",
        "/v1/chat/completions"]
    assert all(call["url"].startswith("http://127.0.0.1:8099/") for call in opener.calls)
    assert [call["method"] for call in opener.calls[:3]] == ["GET", "GET", "GET"]
    body = opener.calls[-1]["body"]
    assert body["messages"][1]["content"] == json.dumps(
        sent["untrusted_data"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert set(body) == {"model", "messages", "temperature", "seed", "max_tokens",
                         "stream", "response_format"}
    assert {key: body[key] for key in ("model", "temperature", "seed", "max_tokens", "stream")} == {
        "model": MODEL, "temperature": 0, "seed": 42, "max_tokens": 1024,
        "stream": False}
    json_schema = body["response_format"]["json_schema"]
    assert json_schema["strict"] is True
    item = json_schema["schema"]["properties"]["judgments"]["items"]
    assert list(item["properties"]) == [
        "evidence_ref", "evidence_quote", "rationale", "id", "verdict"]
    assert item["required"] == ["id", "rationale", "verdict"]
    assert "$schema" not in json.dumps(json_schema) and "title" not in json.dumps(json_schema)
    assert result["model"] == MODEL and result["status"] == 200
    assert result["finish_reason"] == "stop" and result["usage"] == {
        "input_tokens": 9, "output_tokens": 5}
    receipt = result["transport_receipt"]
    assert receipt["input_tokens"] == 4 and receipt["input_token_method"] == "apply_template"
    assert receipt["identity"] == {"model_id": MODEL, "build_info": "build-1",
                                    "n_ctx": 16_384, "model_path": "model.gguf"}
    assert len(receipt["response_schema_sha256"]) == 64
    assert len(receipt["chat_template_sha256"]) == 64
    assert receipt["server_start_args_sha256"] == start_args_sha
    assert receipt["opener_injected"] is True
    assert receipt["request"]["stream"] is False
    assert "reasoning" not in receipt["request"] and "reasoning_format" not in receipt["request"]
    assert receipt["reasoning_policy"] == {"client_setting": "off",
                                           "server_requirement": "--reasoning off",
                                           "request_override_sent": False}


def test_think_block_in_content_is_rejected_without_stripping_or_persistence(tmp_path):
    from pathlib import Path
    from smtw.claims.local_judge import LocalConfig, LocalJudgeRunError, run_local_judge

    fixture = Path(__file__).parent / "fixtures" / "local-judge-d1"
    valid = json.dumps({"judgments": [{"id": "d1-01", "rationale": "No evidence.",
                                      "verdict": "not_enough_evidence"}]})
    opener = FakeOpener(routes(chat_value=chat(content="<think>RAW_THINK_CANARY</think>" + valid)))
    config = LocalConfig(address="http://localhost:8099", model=MODEL, deadline_seconds=180,
                         input_tokens=14000, output_tokens=1024, json_mode="schema",
                         protocol="openai-compatible", seed=42, temperature=0, reasoning="off")
    output = tmp_path / "out"
    with pytest.raises(LocalJudgeRunError) as raised:
        run_local_judge(fixture / "packets", scope_file=fixture / "scope.json",
                        output_root=output, config=config, transport=transport(opener), max_calls=12)
    assert raised.value.result["error"] == "local_invalid_response"
    assert raised.value.result["not_evaluated"] == 12
    assert sum(call["path"] == "/v1/chat/completions" for call in opener.calls) == 1
    assert not list(output.rglob("judgments-*.json"))
    for path in output.rglob("*"):
        if path.is_file():
            assert b"RAW_THINK_CANARY" not in path.read_bytes()
    receipt = json.loads((output / "calls.jsonl").read_text(encoding="utf-8"))["transport_receipt"]
    assert receipt["reasoning_policy"]["request_override_sent"] is False
    assert "reasoning" not in receipt["request"]


@pytest.mark.parametrize("address", [
    "https://127.0.0.1:8099", "http://127.0.0.2:8099", "http://example.test:8099",
    "http://user@127.0.0.1:8099", "http://127.0.0.1:8099/path", "http://127.0.0.1",
])
def test_non_loopback_or_ambiguous_endpoint_is_rejected_before_open(address):
    opener = FakeOpener({})
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request(address=address))
    assert_safe_error(caught.value, stage="preflight", kind="invalid_endpoint")
    assert opener.calls == []


def test_literal_loopback_forms_are_canonical_without_dns():
    assert validate_loopback_address("http://localhost:9") == "http://127.0.0.1:9"
    assert validate_loopback_address("http://127.0.0.1:9") == "http://127.0.0.1:9"
    assert validate_loopback_address("http://[::1]:9") == "http://[::1]:9"


def test_models_and_props_identity_are_fail_closed_before_generation():
    model_routes = routes()
    model_routes["/v1/models"] = [FakeResponse({"data": [{"id": "other"}]})]
    opener = FakeOpener(model_routes)
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request())
    assert_safe_error(caught.value, stage="models", kind="identity_mismatch", status=200)
    assert "/v1/chat/completions" not in [call["path"] for call in opener.calls]

    props_routes = routes()
    props_routes["/props"] = [FakeResponse({"build_info": "build-1", "n_ctx": 16_384,
                                            "model_path": "different.gguf"})]
    opener = FakeOpener(props_routes)
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request(expected_identity={"model_path": "model.gguf"}))
    assert_safe_error(caught.value, stage="props", kind="identity_mismatch", status=200)


@pytest.mark.parametrize("model_data", [
    [{"id": MODEL}, "malformed"],
    [{"id": MODEL}, {}],
    [{"id": MODEL}, {"id": 7}],
])
def test_models_rejects_malformed_extra_items_instead_of_filtering_them(model_data):
    malformed_routes = routes()
    malformed_routes["/v1/models"] = [FakeResponse({"data": model_data})]
    opener = FakeOpener(malformed_routes)
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request())
    assert_safe_error(caught.value, stage="models", kind="identity_mismatch", status=200)
    assert [call["path"] for call in opener.calls] == ["/health", "/v1/models"]


def test_token_limit_rejects_without_truncation_or_generation():
    opener = FakeOpener(routes(token_count=14_001))
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request())
    assert_safe_error(caught.value, stage="tokenize", kind="rejected_input_too_large", status=200)
    assert caught.value.receipt["identity"] == {
        "model_id": MODEL, "build_info": "build-1", "n_ctx": 16_384,
        "model_path": "model.gguf"}
    assert len(caught.value.receipt["response_schema_sha256"]) == 64
    assert caught.value.receipt["request"]["max_tokens"] == 1_024
    assert caught.value.receipt["input_tokens"] == 14_001
    assert "toy" not in json.dumps(caught.value.receipt) and "messages" not in caught.value.receipt
    assert "/v1/chat/completions" not in [call["path"] for call in opener.calls]


@pytest.mark.parametrize("prompt", ["", "   \t\r\n"])
def test_apply_template_rejects_empty_or_whitespace_prompt_before_tokenize(prompt):
    opener = FakeOpener(routes(apply=FakeResponse({"prompt": prompt})))
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request())
    assert_safe_error(caught.value, stage="apply_template", kind="invalid_response", status=200)
    assert "/tokenize" not in [call["path"] for call in opener.calls]
    assert "/v1/chat/completions" not in [call["path"] for call in opener.calls]


def test_tokenize_rejects_empty_token_list_before_chat():
    opener = FakeOpener(routes(token_count=0))
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request())
    assert_safe_error(caught.value, stage="tokenize", kind="invalid_response", status=200)
    assert "/v1/chat/completions" not in [call["path"] for call in opener.calls]


@pytest.mark.parametrize("status", [404, 405, 501])
def test_missing_apply_template_uses_recorded_conservative_estimate(status):
    missing = HTTPError("http://127.0.0.1:8099/apply-template", status, "secret", {}, io.BytesIO(b"secret"))
    opener = FakeOpener(routes(apply=missing))
    result = transport(opener)(request())
    receipt = result["transport_receipt"]
    assert receipt["input_token_method"] == "conservative_fallback"
    assert receipt["input_tokens"] > receipt["tokenizer_tokens"]
    assert receipt["tokenizer_tokens"] == 4
    assert receipt["estimated_template_tokens"] == receipt["input_tokens"]
    assert opener.calls[4]["path"] == "/tokenize"


def test_fallback_counts_full_long_template_and_rejects_before_chat():
    missing = HTTPError("http://127.0.0.1:8099/apply-template", 404, "missing", {}, io.BytesIO())
    fallback_routes = routes(apply=missing)
    fallback_routes["/props"] = [FakeResponse({"build_info": "build-1", "n_ctx": 16_384,
                                               "model_path": "model.gguf",
                                               "chat_template": "t" * 14_000})]
    opener = FakeOpener(fallback_routes)
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request())
    assert_safe_error(caught.value, stage="tokenize", kind="rejected_input_too_large", status=200)
    assert caught.value.receipt["estimated_template_tokens"] > 14_000
    assert "/v1/chat/completions" not in [call["path"] for call in opener.calls]


def test_missing_template_fallback_uses_full_context_bound_and_rejects():
    missing = HTTPError("http://127.0.0.1:8099/apply-template", 404, "missing", {}, io.BytesIO())
    fallback_routes = routes(apply=missing)
    fallback_routes["/props"] = [FakeResponse({"build_info": "build-1", "n_ctx": 16_384,
                                               "model_path": "model.gguf"})]
    with pytest.raises(LocalHttpError) as caught:
        transport(FakeOpener(fallback_routes))(request())
    assert caught.value.record["kind"] == "rejected_input_too_large"
    assert caught.value.receipt["estimated_template_tokens"] == 16_384


def test_props_context_must_fit_input_and_output_caps():
    short_routes = routes()
    short_routes["/props"] = [FakeResponse({"build_info": "build-1", "n_ctx": 15_023,
                                            "model_path": "model.gguf", "chat_template": "template"})]
    opener = FakeOpener(short_routes)
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request())
    assert_safe_error(caught.value, stage="props", kind="identity_mismatch", status=200)
    assert "/apply-template" not in [call["path"] for call in opener.calls]


class CanaryBody(io.BytesIO):
    def read(self, *_args, **_kwargs):
        raise AssertionError("HTTP error body must not be read")


@pytest.mark.parametrize(("status", "kind"), [
    (302, "redirect_refused"), (400, "http_error"), (429, "http_error"),
    (500, "http_error"), (503, "http_error"),
])
def test_http_error_and_redirect_never_read_or_record_body(status, kind):
    failure = HTTPError("http://127.0.0.1:8099/health", status, "secret exception", {},
                        CanaryBody(b"secret body"))
    opener = FakeOpener({"/health": [failure]})
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request())
    assert_safe_error(caught.value, stage="health", kind=kind, status=status)


class CanaryResponse(FakeResponse):
    def read(self, *_args, **_kwargs):
        raise AssertionError("non-success response body must not be read")


def test_returned_non_success_response_body_is_not_read():
    opener = FakeOpener({"/health": [CanaryResponse(b"secret body", status=500)]})
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request())
    assert_safe_error(caught.value, stage="health", kind="http_error", status=500)


@pytest.mark.parametrize("status", [201, 204])
def test_expected_endpoints_require_exact_http_200_without_reading_body(status):
    opener = FakeOpener({"/health": [CanaryResponse(b"secret body", status=status)]})
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request())
    assert_safe_error(caught.value, stage="health", kind="http_error", status=status)


class DropResponse(FakeResponse):
    def read(self, _limit=-1):
        raise ConnectionError("secret read failure")


def test_read_drop_at_any_stage_marks_unknown_and_holds_gate():
    gate = LocalHttpGate()
    opener = FakeOpener({"/health": [DropResponse({"status": "ok"})]})
    with pytest.raises(LocalHttpError) as caught:
        transport(opener, gate=gate)(request())
    assert_safe_error(caught.value, stage="health", kind="connection_dropped")
    assert caught.value.record["server_computation_ended"] == "UNKNOWN"
    assert gate.held is True


class FakeClock:
    def __init__(self):
        self.value = 10.0

    def __call__(self):
        return self.value


def test_whole_request_deadline_and_run_deadline_poison_shared_gate():
    clock = FakeClock()
    gate = LocalHttpGate()

    def cross_deadline(_request, _timeout):
        clock.value += 181
        return FakeResponse({"status": "ok"})

    first = FakeOpener({"/health": [cross_deadline]})
    with pytest.raises(LocalHttpError) as caught:
        transport(first, clock=clock, gate=gate)(request())
    assert_safe_error(caught.value, stage="health", kind="deadline_exceeded")
    assert caught.value.record["server_computation_ended"] == "UNKNOWN"

    second = FakeOpener({})
    with pytest.raises(LocalHttpError) as held:
        transport(second, clock=clock, gate=gate)(request(request_id="request-2"))
    assert held.value.record["kind"] == "held" and second.calls == []

    clock = FakeClock()
    opener = FakeOpener({})
    with pytest.raises(LocalHttpError) as run_expired:
        transport(opener, clock=clock)(request(run_deadline=9.0))
    assert run_expired.value.record["kind"] == "deadline_exceeded" and opener.calls == []


class BlockingResponse(FakeResponse):
    def __init__(self):
        super().__init__({"status": "ok"})
        self.released = threading.Event()
        self.read_started = threading.Event()

    def read(self, _limit=-1):
        self.read_started.set()
        self.released.wait(2)
        return self.data

    def close(self):
        self.closed = True
        self.released.set()


class BlockingCloseResponse(FakeResponse):
    def __init__(self):
        super().__init__({"status": "ok"})
        self.read_started = threading.Event()
        self.read_release = threading.Event()
        self.close_entered = threading.Event()
        self.close_release = threading.Event()

    def read(self, _limit=-1):
        self.read_started.set()
        self.read_release.wait(2)
        return self.data

    def close(self):
        self.close_entered.set()
        self.close_release.wait(2)
        self.closed = True


class AdvancingClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        self.value += 61.0
        return self.value


class TriggeredClock:
    def __init__(self, trigger):
        self.trigger = trigger

    def __call__(self):
        return 181.0 if self.trigger.is_set() else 0.0


def test_delayed_worker_cannot_open_after_watchdog_has_held(monkeypatch):
    targets = []

    class DelayedThread:
        def __init__(self, *, target, name, daemon):
            assert name == "smtw-local-http" and daemon is True
            self.target = target
            targets.append(target)

        def start(self):
            pass

    monkeypatch.setattr(local_http, "_Thread", DelayedThread)
    opener = FakeOpener({})
    gate = LocalHttpGate()
    with pytest.raises(LocalHttpError) as caught:
        transport(opener, clock=AdvancingClock(), gate=gate)(request())
    assert caught.value.record["kind"] == "deadline_exceeded"
    assert caught.value.record["server_computation_ended"] == "UNKNOWN"
    assert gate.held is True and opener.calls == [] and len(targets) == 1

    targets[0]()
    assert opener.calls == []


def test_deadline_during_blocked_read_closes_response_and_holds_next_opener():
    blocked = BlockingResponse()
    gate = LocalHttpGate()
    opener = FakeOpener({"/health": [blocked]})
    with pytest.raises(LocalHttpError) as caught:
        transport(opener, clock=TriggeredClock(blocked.read_started), gate=gate)(request())
    assert caught.value.record["kind"] == "deadline_exceeded"
    assert caught.value.record["server_computation_ended"] == "UNKNOWN"
    assert blocked.released.wait(1) and blocked.closed and gate.held is True

    later = FakeOpener({})
    with pytest.raises(LocalHttpError) as held:
        transport(later, gate=gate)(request(request_id="later"))
    assert held.value.record["kind"] == "held" and later.calls == []


def test_deadline_returns_while_response_close_itself_is_blocked():
    blocked = BlockingCloseResponse()
    gate = LocalHttpGate()
    started = time.monotonic()
    with pytest.raises(LocalHttpError) as caught:
        transport(FakeOpener({"/health": [blocked]}),
                  clock=TriggeredClock(blocked.read_started), gate=gate)(request())
    assert time.monotonic() - started < 0.5
    assert caught.value.record["kind"] == "deadline_exceeded"
    assert caught.value.record["server_computation_ended"] == "UNKNOWN"
    assert blocked.close_entered.wait(0.5) and gate.held is True

    later = FakeOpener({})
    with pytest.raises(LocalHttpError) as held:
        transport(later, gate=gate)(request(request_id="later"))
    assert held.value.record["kind"] == "held" and later.calls == []
    blocked.read_release.set()
    blocked.close_release.set()


def test_stop_during_blocked_read_closes_response_and_holds_new_instances():
    blocked = BlockingResponse()
    opener = FakeOpener({"/health": [blocked]})
    gate = LocalHttpGate()
    stop = blocked.read_started.is_set
    with pytest.raises(LocalHttpError) as caught:
        transport(opener, stop_requested=stop, gate=gate)(request())
    assert_safe_error(caught.value, stage="health", kind="stopped")
    assert blocked.released.wait(1) and blocked.closed
    assert caught.value.record["server_computation_ended"] == "UNKNOWN"

    later = FakeOpener({})
    with pytest.raises(LocalHttpError) as held:
        transport(later, gate=gate)(request(request_id="later"))
    assert held.value.record["kind"] == "held" and later.calls == []


def test_chat_connection_drop_holds_process_gate_and_sanitizes_exception():
    opener = FakeOpener(routes(chat_value=chat()))
    opener.routes["/v1/chat/completions"] = [ConnectionError("secret exception and body")]
    gate = LocalHttpGate()
    with pytest.raises(LocalHttpError) as caught:
        transport(opener, gate=gate)(request())
    assert_safe_error(caught.value, stage="chat", kind="connection_dropped")
    assert caught.value.record["server_computation_ended"] == "UNKNOWN"
    assert gate.held is True


def test_default_gate_is_shared_by_new_transport_instances(monkeypatch):
    gate = LocalHttpGate()
    monkeypatch.setattr(local_http, "_GLOBAL_GATE", gate)
    first = FakeOpener(routes())
    first.routes["/v1/chat/completions"] = [ConnectionError("drop")]
    with pytest.raises(LocalHttpError):
        LocalHttpTransport(opener=first, wait_interval=0.001)(request())

    second = FakeOpener({})
    with pytest.raises(LocalHttpError) as held:
        LocalHttpTransport(opener=second, wait_interval=0.001)(request(request_id="new"))
    assert held.value.record["kind"] == "held" and second.calls == []


def test_duplicate_in_flight_is_rejected_without_second_open():
    blocked = BlockingResponse()
    first_opener = FakeOpener({"/health": [blocked],
                               "/v1/models": [FakeResponse({"data": [{"id": MODEL}]})],
                               "/props": [FakeResponse({"build_info": "b", "n_ctx": 16_384,
                                                        "model_path": "m"})],
                               "/apply-template": [FakeResponse({"prompt": "p"})],
                               "/tokenize": [FakeResponse({"tokens": [1]})],
                               "/v1/chat/completions": [FakeResponse(chat())]})
    gate = LocalHttpGate()
    errors = []

    def run_first():
        try:
            transport(first_opener, gate=gate)(request())
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_first, daemon=True)
    worker.start()
    assert blocked.released.wait(0.02) is False

    second_opener = FakeOpener({})
    with pytest.raises(LocalHttpError) as duplicate:
        transport(second_opener, gate=gate)(request(request_id="request-2"))
    assert duplicate.value.record["kind"] == "in_flight" and second_opener.calls == []
    blocked.released.set()
    worker.join(1)
    assert not worker.is_alive() and errors == []


@pytest.mark.parametrize(("held", "in_flight", "kind"), [
    (False, True, "in_flight"), (True, True, "held"),
])
def test_gate_rejection_preserves_active_receipt_without_attaching_it_to_error(
        held, in_flight, kind):
    gate = LocalHttpGate()
    gate.held = held
    gate.in_flight = in_flight
    caller = transport(FakeOpener({}), gate=gate)
    active_receipt = {"input_token_method": "active"}
    caller.last_receipt = active_receipt

    with pytest.raises(LocalHttpError) as caught:
        caller(request(request_id="duplicate"))
    assert caught.value.record["kind"] == kind
    assert caught.value.receipt is None
    assert caller.last_receipt is active_receipt


@pytest.mark.parametrize(("replacement", "kind"), [
    (chat(finish="length"), "finish_reason_length"),
    (chat(model="other"), "identity_mismatch"),
    ({"model": MODEL, "choices": [], "usage": {}}, "invalid_response"),
    (chat(usage={"prompt_tokens": 1, "completion_tokens": "5"}), "invalid_response"),
])
def test_strict_chat_response_validation(replacement, kind):
    opener = FakeOpener(routes(chat_value=replacement))
    with pytest.raises(LocalHttpError) as caught:
        transport(opener)(request())
    assert_safe_error(caught.value, stage="chat", kind=kind, status=200)
    assert caught.value.receipt["identity"]["model_id"] == MODEL
    assert caught.value.receipt["identity"]["n_ctx"] == 16_384
    assert len(caught.value.receipt["response_schema_sha256"]) == 64
    assert caught.value.receipt["request"] == {
        "model": MODEL, "temperature": 0, "seed": 42, "max_tokens": 1_024,
        "stream": False, "response_format": "json_schema", "strict": True}
    assert caught.value.receipt["reasoning_policy"] == {
        "client_setting": "off", "server_requirement": "--reasoning off",
        "request_override_sent": False}
    assert caught.value.receipt["input_tokens"] == 4
    assert "toy" not in json.dumps(caught.value.receipt) and "messages" not in caught.value.receipt


def test_direct_opener_ignores_proxy_environment_and_disables_redirects(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.setenv(name, "http://proxy.invalid:9999")
    monkeypatch.setattr(urllib.request, "getproxies",
                        lambda: pytest.fail("environment proxy lookup is forbidden"))
    opener = build_direct_opener()
    proxies = [handler for handler in opener.handlers if isinstance(handler, ProxyHandler)]
    # An empty ProxyHandler registers no protocol callbacks, so build_opener
    # omits it entirely instead of falling back to environment proxies.
    assert proxies == []
    redirect = next(handler for handler in opener.handlers
                    if type(handler).__name__ == "_NoRedirect")
    assert redirect.redirect_request(None, None, 302, "", {}, "http://example.test") is None

    fake = FakeOpener(routes())
    transport(fake)(request(address="http://localhost:8099"))
    assert all(call["url"].startswith("http://127.0.0.1:8099/") for call in fake.calls)
