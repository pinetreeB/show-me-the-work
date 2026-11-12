"""Fail-closed stdlib HTTP transport for the local claims judge.

The transport is deliberately small and injectable.  Tests supply an opener;
the production opener disables proxies and redirects and accepts only explicit
loopback HTTP endpoints.  No error path retains response bodies or exception
text.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import socket
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


DEADLINE_SECONDS = 180
INPUT_TOKEN_LIMIT = 14_000
OUTPUT_TOKEN_LIMIT = 1_024
TEMPLATE_FRAMING_BYTES = 64
MAX_RESPONSE_BYTES = 1_048_576
_Thread = threading.Thread


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_direct_opener():
    """Build an opener that ignores environment proxies and rejects redirects."""
    return build_opener(ProxyHandler({}), _NoRedirect())


def validate_loopback_address(address: str) -> str:
    """Return a canonical loopback base URL without performing DNS resolution."""
    if not isinstance(address, str):
        raise ValueError("local address must be an explicit loopback HTTP endpoint")
    parsed = urlsplit(address)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("local address has an invalid port") from exc
    if (parsed.scheme != "http" or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or port is None or not 1 <= port <= 65535):
        raise ValueError("local address must be an explicit loopback HTTP endpoint")
    # Never resolve the localhost spelling.  The connection target is the
    # literal IPv4 loopback address, so a hosts-file or DNS change cannot escape.
    host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
    return f"http://[{host}]:{port}" if host == "::1" else f"http://{host}:{port}"


@dataclass
class _Failure(Exception):
    stage: str
    kind: str
    http_status: int | None = None
    server_computation_ended: str = "NOT_STARTED"
    hold: bool = False


class LocalHttpError(RuntimeError):
    """Sanitized transport failure; ``record`` is safe to persist."""

    def __init__(self, record: dict[str, Any], receipt: dict[str, Any] | None = None):
        super().__init__("local HTTP transport failed")
        self.record = record
        self.receipt = receipt


class LocalHttpGate:
    """Process gate; production transports share the module-level instance."""

    def __init__(self):
        self.lock = threading.Lock()
        self.in_flight = False
        self.held = False


_GLOBAL_GATE = LocalHttpGate()


def _strict_object(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = child
    return value


def _decode_json(data: bytes) -> Any:
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    return json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def _abort_in_background(response) -> None:
    """Best-effort active-I/O abort without ever blocking the watchdog."""
    def abort():
        # urllib HTTPResponse normally exposes the socket through this chain.
        # Every operation stays on a disposable daemon: wrappers may serialize
        # close() behind an active read lock.
        fp = getattr(response, "fp", None)
        raw = getattr(fp, "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
        try:
            response.close()
        except Exception:
            pass

    _Thread(target=abort, name="smtw-local-http-abort", daemon=True).start()


def _ordered_schema(schema: dict) -> dict:
    """Remove annotations and put evidence/rationale before the verdict."""
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(child) for key, child in value.items() if key not in {"$schema", "title"}}
        if isinstance(value, list):
            return [clean(child) for child in value]
        return value

    result = clean(schema)
    item = result.get("properties", {}).get("judgments", {}).get("items", {})
    properties = item.get("properties", {})
    if isinstance(properties, dict):
        order = ("evidence_ref", "evidence_quote", "rationale", "id", "verdict")
        item["properties"] = {key: properties[key] for key in order if key in properties}
        item["properties"].update({key: value for key, value in properties.items() if key not in order})
    required = item.get("required")
    if isinstance(required, list):
        item["required"] = [key for key in ("id", "rationale", "verdict") if key in required]
    return result


class LocalHttpTransport:
    """Callable OpenAI-compatible local transport with one in-flight request."""

    def __init__(self, *, opener=None, clock: Callable[[], float] = time.monotonic,
                 stop_requested: Callable[[], bool] = lambda: False,
                 wait_interval: float = 0.05, gate: LocalHttpGate | None = None):
        if not callable(clock) or not callable(stop_requested):
            raise TypeError("clock and stop_requested must be callable")
        if not isinstance(wait_interval, (int, float)) or not math.isfinite(wait_interval) or wait_interval <= 0:
            raise ValueError("wait_interval must be positive")
        self._opener = opener if opener is not None else build_direct_opener()
        self._opener_injected = opener is not None
        self._clock = clock
        self._stop_requested = stop_requested
        self._wait_interval = float(wait_interval)
        self._gate = _GLOBAL_GATE if gate is None else gate
        if not isinstance(self._gate, LocalHttpGate):
            raise TypeError("gate must be a LocalHttpGate")
        self.last_receipt: dict[str, Any] | None = None

    @property
    def held(self) -> bool:
        with self._gate.lock:
            return self._gate.held

    def _claim(self, request_id: str, claim_ids: list[str], started: float) -> None:
        with self._gate.lock:
            if self._gate.held:
                raise self._public_error(request_id, claim_ids, started,
                                         _Failure("preflight", "held", server_computation_ended="UNKNOWN"),
                                         include_receipt=False)
            if self._gate.in_flight:
                raise self._public_error(request_id, claim_ids, started,
                                         _Failure("preflight", "in_flight"), include_receipt=False)
            self._gate.in_flight = True

    def _release(self) -> None:
        with self._gate.lock:
            if not self._gate.held:
                self._gate.in_flight = False

    def _public_error(self, request_id: str, claim_ids: list[str], started: float,
                      failure: _Failure, *, include_receipt: bool = True) -> LocalHttpError:
        elapsed = max(0, round((self._clock() - started) * 1000))
        return LocalHttpError({"request_id": request_id, "claim_ids": claim_ids,
                               "stage": failure.stage, "kind": failure.kind,
                               "http_status": failure.http_status, "elapsed_ms": elapsed,
                               "not_evaluated": True,
                               "server_computation_ended": failure.server_computation_ended},
                              self.last_receipt if include_receipt else None)

    def _check(self, stage: str, deadline: float, *, active: bool = False) -> None:
        if self._stop_requested():
            raise _Failure(stage, "stopped", server_computation_ended="UNKNOWN" if active else "NOT_STARTED",
                           hold=active)
        if self._clock() >= deadline:
            raise _Failure(stage, "deadline_exceeded",
                           server_computation_ended="UNKNOWN" if active else "NOT_STARTED", hold=active)

    def _exchange(self, request: Request, *, stage: str, deadline: float,
                  computation_may_continue: bool = False) -> tuple[int, bytes]:
        """Open and read in a daemon so stop/deadline can close active I/O."""
        self._check(stage, deadline)
        state: dict[str, Any] = {}
        done = threading.Event()
        cancelled = threading.Event()
        launch_lock = threading.Lock()

        def work():
            response = None
            try:
                with launch_lock:
                    if cancelled.is_set():
                        return
                    try:
                        self._check(stage, deadline)
                    except _Failure as failure:
                        state["error"] = failure
                        return
                    if cancelled.is_set():
                        return
                    state["open_started"] = True
                timeout = max(0.001, deadline - self._clock())
                if cancelled.is_set():
                    return
                response = self._opener.open(request, timeout=timeout)
                state["response"] = response
                if cancelled.is_set():
                    return
                status = response.getcode() if hasattr(response, "getcode") else response.status
                if type(status) is not int or status != 200:
                    state["value"] = (status, b"")
                    return
                body = response.read(MAX_RESPONSE_BYTES + 1)
                state["value"] = (status, body)
            except BaseException as exc:  # translated below without retaining text
                if isinstance(exc, HTTPError):
                    _abort_in_background(exc)
                state["error"] = exc
            finally:
                done.set()
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass

        worker = _Thread(target=work, name="smtw-local-http", daemon=True)
        worker.start()
        while not done.is_set():
            try:
                self._check(stage, deadline, active=True)
            except _Failure:
                with launch_lock:
                    cancelled.set()
                response = state.get("response")
                if response is not None:
                    _abort_in_background(response)
                raise
            done.wait(self._wait_interval)
        # Crossing the whole-request deadline poisons the process gate even if
        # the worker happened to finish between watchdog polls.  We cannot use
        # that race to assert that server-side work ended before the deadline.
        self._check(stage, deadline, active=True)
        error = state.get("error")
        if error is not None:
            if isinstance(error, HTTPError):
                status = error.code if isinstance(error.code, int) else None
                if status is not None and 300 <= status < 400:
                    raise _Failure(stage, "redirect_refused", status, "CONFIRMED")
                raise _Failure(stage, "http_error", status, "CONFIRMED")
            if computation_may_continue or "response" in state:
                raise _Failure(stage, "connection_dropped", server_computation_ended="UNKNOWN", hold=True)
            raise _Failure(stage, "transport_error")
        status, body = state["value"]
        if type(status) is not int or status != 200:
            kind = "redirect_refused" if type(status) is int and 300 <= status < 400 else "http_error"
            raise _Failure(stage, kind, status if type(status) is int else None, "CONFIRMED")
        if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
            raise _Failure(stage, "invalid_response", status, "CONFIRMED")
        return status, body

    def _json(self, base: str, path: str, *, stage: str, deadline: float,
              payload: dict | None = None, computation_may_continue: bool = False) -> Any:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False,
                                                       separators=(",", ":")).encode("utf-8")
        request = Request(base + path, data=data,
                          headers={"Accept": "application/json", "Content-Type": "application/json"},
                          method="GET" if data is None else "POST")
        status, body = self._exchange(request, stage=stage, deadline=deadline,
                                      computation_may_continue=computation_may_continue)
        try:
            return _decode_json(body)
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
            raise _Failure(stage, "invalid_response", status, "CONFIRMED") from None

    @staticmethod
    def _request_context(request: dict) -> tuple[str, list[str]]:
        request_id = request.get("request_id") or request.get("packet_sha256") or "unknown"
        claim_ids = request.get("claim_ids", [])
        if not isinstance(request_id, str) or not request_id or not isinstance(claim_ids, list):
            return "unknown", []
        return request_id, [item for item in claim_ids if isinstance(item, str)]

    @staticmethod
    def _validate_request(request: dict) -> None:
        exact = {"deadline_seconds": DEADLINE_SECONDS, "input_tokens": INPUT_TOKEN_LIMIT,
                 "output_tokens": OUTPUT_TOKEN_LIMIT, "seed": 42, "temperature": 0,
                 "reasoning": "off", "protocol": "openai-compatible", "json_mode": "schema"}
        if not isinstance(request, dict) or any(request.get(key) != value for key, value in exact.items()):
            raise _Failure("preflight", "invalid_configuration")
        if any(type(request.get(key)) is not int for key in
               ("deadline_seconds", "input_tokens", "output_tokens", "seed")):
            raise _Failure("preflight", "invalid_configuration")
        if (type(request.get("temperature")) not in {int, float}
                or not math.isfinite(request["temperature"])):
            raise _Failure("preflight", "invalid_configuration")
        if (not isinstance(request.get("model"), str) or not request["model"]
                or not isinstance(request.get("instructions"), str)
                or not isinstance(request.get("untrusted_data"), dict)
                or not isinstance(request.get("response_schema"), dict)):
            raise _Failure("preflight", "invalid_configuration")
        run_deadline = request.get("run_deadline")
        if run_deadline is not None and (type(run_deadline) not in {int, float}
                                         or not math.isfinite(run_deadline)):
            raise _Failure("preflight", "invalid_configuration")
        identity = request.get("expected_identity")
        if identity is not None and (not isinstance(identity, dict)
                                     or any(key not in {"build_info", "n_ctx", "model_path"}
                                            for key in identity)):
            raise _Failure("preflight", "invalid_configuration")
        start_args_sha = request.get("server_start_args_sha256")
        if start_args_sha is not None and (not isinstance(start_args_sha, str)
                                           or re.fullmatch(r"[0-9a-f]{64}", start_args_sha) is None):
            raise _Failure("preflight", "invalid_configuration")

    def __call__(self, request: dict) -> dict:
        started = self._clock()
        request_id, claim_ids = self._request_context(request if isinstance(request, dict) else {})
        self._claim(request_id, claim_ids, started)
        self.last_receipt = None
        try:
            self._validate_request(request)
            try:
                base = validate_loopback_address(request["address"])
            except (KeyError, ValueError):
                raise _Failure("preflight", "invalid_endpoint") from None
            run_deadline = request.get("run_deadline")
            deadline = min(started + DEADLINE_SECONDS,
                           float(run_deadline) if run_deadline is not None else math.inf)
            self._check("preflight", deadline)
            schema = _ordered_schema(request["response_schema"])
            self.last_receipt = {
                "request": {"model": request["model"], "temperature": 0, "seed": 42,
                            "max_tokens": OUTPUT_TOKEN_LIMIT, "stream": False,
                            "response_format": "json_schema", "strict": True},
                "reasoning_policy": {"client_setting": request["reasoning"],
                                     "server_requirement": "--reasoning off",
                                     "request_override_sent": False},
                "server_start_args_sha256": request.get("server_start_args_sha256"),
                "opener_injected": self._opener_injected,
                "response_schema_sha256": hashlib.sha256(json.dumps(
                    schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
            }

            health = self._json(base, "/health", stage="health", deadline=deadline)
            if not isinstance(health, dict) or health.get("status") != "ok":
                raise _Failure("health", "invalid_response", 200, "CONFIRMED")

            models = self._json(base, "/v1/models", stage="models", deadline=deadline)
            model_data = models.get("data") if isinstance(models, dict) else None
            if (not isinstance(model_data, list) or len(model_data) != 1
                    or not isinstance(model_data[0], dict)
                    or not isinstance(model_data[0].get("id"), str)
                    or model_data[0]["id"] != request["model"]):
                raise _Failure("models", "identity_mismatch", 200, "CONFIRMED")

            props = self._json(base, "/props", stage="props", deadline=deadline)
            settings = props.get("default_generation_settings", {}) if isinstance(props, dict) else {}
            n_ctx = props.get("n_ctx") if isinstance(props, dict) else None
            if type(n_ctx) is not int and isinstance(settings, dict):
                n_ctx = settings.get("n_ctx")
            if (not isinstance(props, dict) or not isinstance(props.get("build_info"), (str, dict))
                    or type(n_ctx) is not int or n_ctx < INPUT_TOKEN_LIMIT + OUTPUT_TOKEN_LIMIT
                    or not isinstance(props.get("model_path"), str) or not props["model_path"]):
                raise _Failure("props", "identity_mismatch", 200, "CONFIRMED")
            expected_identity = request.get("expected_identity") or {}
            actual_identity = {"build_info": props["build_info"], "n_ctx": n_ctx,
                               "model_path": props["model_path"]}
            if any(actual_identity.get(key) != value for key, value in expected_identity.items()):
                raise _Failure("props", "identity_mismatch", 200, "CONFIRMED")
            self.last_receipt.update({
                "identity": {"model_id": request["model"], **actual_identity},
                "chat_template_sha256": (hashlib.sha256(props["chat_template"].encode("utf-8")).hexdigest()
                                         if isinstance(props.get("chat_template"), str) else None),
            })

            user_content = json.dumps(request["untrusted_data"], ensure_ascii=False,
                                      sort_keys=True, separators=(",", ":"))
            messages = [{"role": "system", "content": request["instructions"]},
                        {"role": "user", "content": user_content}]
            token_method = "apply_template"
            try:
                applied = self._json(base, "/apply-template", stage="apply_template", deadline=deadline,
                                     payload={"messages": messages, "add_generation_prompt": True})
                if (not isinstance(applied, dict) or not isinstance(applied.get("prompt"), str)
                        or not applied["prompt"].strip()):
                    raise _Failure("apply_template", "invalid_response", 200, "CONFIRMED")
                token_content = applied["prompt"]
            except _Failure as failure:
                if failure.kind != "http_error" or failure.http_status not in {404, 405, 501}:
                    raise
                token_method = "conservative_fallback"
                token_content = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))

            tokenized = self._json(base, "/tokenize", stage="tokenize", deadline=deadline,
                                   payload={"content": token_content, "add_special": True,
                                            "with_pieces": False})
            if (not isinstance(tokenized, dict) or not isinstance(tokenized.get("tokens"), list)
                    or not tokenized["tokens"]
                    or any(type(token) is not int for token in tokenized["tokens"])):
                raise _Failure("tokenize", "invalid_response", 200, "CONFIRMED")
            token_count = len(tokenized["tokens"])
            tokenizer_tokens = token_count
            estimated_template_tokens = None
            if token_method == "conservative_fallback":
                chat_template = props.get("chat_template")
                estimated_template_tokens = (len(token_content.encode("utf-8"))
                                             + len(chat_template.encode("utf-8"))
                                             + TEMPLATE_FRAMING_BYTES
                                             if isinstance(chat_template, str) and chat_template
                                             else n_ctx)
                token_count = max(token_count, estimated_template_tokens)
            self.last_receipt.update({"input_tokens": token_count, "input_token_method": token_method,
                                      "tokenizer_tokens": tokenizer_tokens,
                                      "estimated_template_tokens": estimated_template_tokens})
            if token_count > INPUT_TOKEN_LIMIT:
                raise _Failure("tokenize", "rejected_input_too_large", 200, "CONFIRMED")

            chat_payload = {"model": request["model"], "messages": messages,
                            "temperature": 0, "seed": 42, "max_tokens": OUTPUT_TOKEN_LIMIT,
                            "stream": False,
                            "response_format": {"type": "json_schema", "json_schema": {
                                "name": "claims_judgments", "strict": True, "schema": schema}}}
            result = self._json(base, "/v1/chat/completions", stage="chat", deadline=deadline,
                                payload=chat_payload, computation_may_continue=True)
            if (not isinstance(result, dict) or result.get("model") != request["model"]
                    or not isinstance(result.get("choices"), list) or len(result["choices"]) != 1
                    or not isinstance(result["choices"][0], dict)):
                raise _Failure("chat", "identity_mismatch" if isinstance(result, dict)
                               and result.get("model") != request["model"] else "invalid_response",
                               200, "CONFIRMED")
            choice = result["choices"][0]
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                raise _Failure("chat", "finish_reason_length", 200, "CONFIRMED")
            if finish_reason != "stop" or not isinstance(choice.get("message"), dict) \
                    or not isinstance(choice["message"].get("content"), str):
                raise _Failure("chat", "invalid_response", 200, "CONFIRMED")
            usage = result.get("usage")
            if (not isinstance(usage, dict) or type(usage.get("prompt_tokens")) is not int
                    or type(usage.get("completion_tokens")) is not int
                    or usage["prompt_tokens"] < 0 or usage["completion_tokens"] < 0):
                raise _Failure("chat", "invalid_response", 200, "CONFIRMED")

            self.last_receipt.update({
                "usage": {"input_tokens": usage["prompt_tokens"],
                          "output_tokens": usage["completion_tokens"]},
                "elapsed_ms": max(0, round((self._clock() - started) * 1000)),
            })
            receipt = self.last_receipt
            return {"model": result["model"], "status": 200,
                    "body": choice["message"]["content"],
                    "usage": receipt["usage"], "finish_reason": finish_reason,
                    "transport_receipt": receipt}
        except _Failure as failure:
            if failure.hold:
                with self._gate.lock:
                    self._gate.held = True
            raise self._public_error(request_id, claim_ids, started, failure) from None
        finally:
            self._release()
