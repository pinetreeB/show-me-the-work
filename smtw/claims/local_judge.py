"""Fail-closed local-judge adapter with an injectable transport boundary."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
import uuid

from .advisory import load_sent_items, read_json, validate_judgments, verify_judgments
from .code_identity import code_sha256
from .judge_contract import PROMPT, SCHEMA, contract, validate_contract
from .judge_scope import load_scope
from .packets import sent_sources
from .privacy import load_blocklist, needs_blocklist, validate_packet_privacy


@dataclass(frozen=True)
class LocalConfig:
    """All connection and inference values must be explicitly supplied."""

    address: str
    model: str
    deadline_seconds: int
    input_tokens: int
    output_tokens: int
    json_mode: str
    protocol: str
    seed: int
    temperature: float
    reasoning: str

    def validate(self) -> None:
        strings = ("address", "model", "json_mode", "protocol", "reasoning")
        if any(not isinstance(getattr(self, key), str) or not getattr(self, key).strip()
               for key in strings):
            raise ValueError("local configuration requires explicit non-empty values")
        positive = ("deadline_seconds", "input_tokens", "output_tokens")
        if any(type(getattr(self, key)) is not int or getattr(self, key) < 1 for key in positive):
            raise ValueError("local deadline and token caps must be positive integers")
        if (type(self.seed) is not int or type(self.temperature) not in {int, float}
                or not math.isfinite(self.temperature) or self.temperature < 0):
            raise ValueError("local seed and temperature must be explicit numeric values")
        address_match = re.fullmatch(r"http://(?:127\.0\.0\.1|\[::1\]|localhost):([1-9]\d{0,4})", self.address)
        if (not address_match or int(address_match.group(1)) > 65535 or "@" in self.address):
            raise ValueError("local address must be an explicit loopback HTTP address without credentials")
        if self.json_mode not in {"schema", "json_object"}:
            raise ValueError("unsupported local JSON mode")
        if self.protocol not in {"openai-compatible", "llama-cpp-native", "synthetic-v1"}:
            raise ValueError("unsupported local protocol")
        if self.reasoning not in {"off", "separate"}:
            raise ValueError("local reasoning must be off or separate")

    def validate_d2_runtime(self) -> None:
        """The real CLI may use only the environment-approved D2 settings."""
        self.validate()
        expected = {"model": "judge-qwen38-27b", "deadline_seconds": 180,
                    "input_tokens": 14000, "output_tokens": 1024, "json_mode": "schema",
                    "protocol": "openai-compatible", "seed": 42, "temperature": 0,
                    "reasoning": "off"}
        if any(getattr(self, key) != value for key, value in expected.items()):
            raise ValueError("local D2 runtime requires the approved explicit settings")


def compatibility_schema() -> dict:
    """Return the ordered, model-visible envelope derived from the frozen schema.

    A quote remains optional: a ``not_enough_evidence`` judgment must not invent
    one.  The receive-side verifier still requires both quote and reference for
    substantive judgments, so this relaxation cannot display an ungrounded
    supported or contradicted result.
    """
    def without_meta(value):
        if isinstance(value, dict):
            return {key: without_meta(child) for key, child in value.items()
                    if key not in {"$schema", "title"}}
        if isinstance(value, list):
            return [without_meta(child) for child in value]
        return value

    canonical = without_meta(json.loads(SCHEMA.read_text(encoding="utf-8")))
    item = canonical["items"]
    properties = item["properties"]
    # JSON object order is deliberate guidance for constrained generation.
    item["properties"] = {key: properties[key]
                          for key in ("evidence_ref", "evidence_quote", "rationale", "id", "verdict")}
    item["required"] = [key for key in ("id", "rationale", "verdict") if key in item["required"]]
    return {"type": "object", "properties": {"judgments": {"type": "array", "items": item}},
            "required": ["judgments"], "additionalProperties": False}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _target_evidence_complete(item: dict) -> bool:
    """Derive coverage only from the sent excerpt, never an item boolean."""
    branches = {item.get("branch")} | set(item.get("branches", []))
    # A complete excerpt elsewhere cannot turn an incomplete repository search
    # or timeout into substantive evidence for the original target.
    if branches & {"repository_search_incomplete", "timed_out"}:
        return False
    targets = item.get("target_evidence")
    if not targets:
        # Fixed-commit recovery needs positive evidence for the target range;
        # an absent metadata field is never evidence.
        return not (branches & {"pointer_missing", "no_read_of_file", "partial_range"}
                    or item.get("fixed_commit") is True
                    or any(entry.get("source") == "fixed_commit" for entry in item.get("excerpts", [])))
    if not isinstance(targets, list):
        return False
    excerpts = item.get("excerpts", [])
    for target in targets:
        if (not isinstance(target, dict) or not all(type(target.get(key)) is int and target[key] > 0
                                                    for key in ("start_line", "end_line", "excerpt_index"))):
            return False
        if not isinstance(target.get("path"), str) or target["start_line"] > target["end_line"]:
            return False
        excerpt = next((entry for entry in excerpts if entry.get("ref") == target["excerpt_index"]), None)
        if not excerpt or excerpt.get("path") != target["path"]:
            return False
        numbered_pairs = [(int(match.group(1)), match.group(2)) for match in re.finditer(
            r"(?m)^(\d+): ?(.*)$", excerpt.get("text", ""))]
        numbered = dict(numbered_pairs)
        if len(numbered) != len(numbered_pairs):
            return False
        required = range(target["start_line"], target["end_line"] + 1)
        if any(line not in numbered or "[REDACTED:" in numbered[line].upper() for line in required):
            return False
    return True


def _model_packet(packet: dict) -> dict:
    """Copy packet data while removing deterministic checker hints from items.

    This is deliberately a view for the model only.  N2 coverage and receipt
    checks continue to inspect the original, hash-pinned packet.
    """
    visible = json.loads(json.dumps(packet))

    for item in visible.get("data_block", []):
        if not isinstance(item, dict):
            continue
        item.pop("branch", None)
        item.pop("branches", None)
        item.pop("verdict", None)
        item.pop("reason_code", None)
        for field in ("fixed_commit", "target_evidence", "other_file_evidence", "atom", "check"):
            item.pop(field, None)
    return visible


def _request(config: LocalConfig, packet: dict, packet_sha256: str, *, request_id: str,
             claim_ids: list[str], run_deadline: float, server_start_args_sha256: str | None) -> dict:
    visible_packet = _model_packet(packet)
    return {"protocol": config.protocol, "address": config.address, "model": config.model,
            "deadline_seconds": config.deadline_seconds, "input_tokens": config.input_tokens,
            "output_tokens": config.output_tokens, "json_mode": config.json_mode,
            "seed": config.seed, "temperature": config.temperature, "reasoning": config.reasoning,
            "response_schema": compatibility_schema(), "packet_sha256": packet_sha256,
            "request_id": request_id, "claim_ids": claim_ids, "run_deadline": run_deadline,
            "server_start_args_sha256": server_start_args_sha256,
            # Packet text remains separate untrusted data; no response field is a path to open.
            "instructions": PROMPT.read_text(encoding="utf-8"),
            "untrusted_data": visible_packet,
            "transmitted_data_sha256": _sha(json.dumps(visible_packet, ensure_ascii=False,
                                                        sort_keys=True, separators=(",", ":")).encode("utf-8"))}


def _object(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate local JSON key")
        value[key] = child
    return value


def _strict_json(body: str):
    return json.loads(body, object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON value")))


def _decode_response(response, expected_model: str, config: LocalConfig):
    if not isinstance(response, dict):
        raise ValueError("local response envelope is invalid")
    if response.get("model") != expected_model:
        raise ValueError("local model identity mismatch")
    status = response.get("status")
    if status in {429, 503}:
        raise ValueError("local overload")
    if status != 200:
        raise ValueError("local response status is invalid")
    allowed = {"model", "status", "body", "usage", "finish_reason", "transport_receipt"}
    if not {"model", "status", "body", "usage"} <= set(response) <= allowed or not isinstance(response["body"], str):
        raise ValueError("local response envelope is invalid")
    if response.get("finish_reason") == "length":
        raise ValueError("local response was truncated")
    usage = response["usage"]
    if (not isinstance(usage, dict) or set(usage) != {"input_tokens", "output_tokens"}
            or any(type(usage[key]) is not int or usage[key] < 0 for key in usage)):
        raise ValueError("local token usage envelope is invalid")
    if usage["input_tokens"] > config.input_tokens or usage["output_tokens"] > config.output_tokens:
        raise ValueError("local token cap exceeded")
    value = _strict_json(response["body"])
    if not isinstance(value, dict) or set(value) != {"judgments"}:
        raise ValueError("local response result envelope is invalid")
    return value["judgments"]


def _write_record(output_root: Path, record: dict) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    with (output_root / "calls.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _safe_transport_receipt(receipt) -> dict | None:
    """Keep known telemetry fields, including metadata verified before failure."""
    if not isinstance(receipt, dict):
        return None
    scalar_fields = ("input_tokens", "input_token_method", "tokenizer_tokens",
                     "estimated_template_tokens", "response_schema_sha256",
                     "server_start_args_sha256", "chat_template_sha256",
                     "opener_injected", "elapsed_ms")
    safe = {key: receipt[key] for key in scalar_fields if key in receipt}
    groups = {
        "request": ("model", "temperature", "seed", "max_tokens", "reasoning",
                    "response_format", "strict"),
        "identity": ("model_id", "build_info", "n_ctx", "model_path"),
        "usage": ("input_tokens", "output_tokens"),
    }
    for group, fields in groups.items():
        if isinstance(receipt.get(group), dict):
            safe[group] = {key: receipt[group][key] for key in fields if key in receipt[group]}
    return safe


class LocalJudgeRunError(ValueError):
    """A stopped local run with only safe, normalized handoff data."""

    def __init__(self, result: dict):
        super().__init__("local judge execution failed; see call receipt")
        self.result = result


def run_local_judge(folder: Path, *, scope_file: Path, output_root: Path, config: LocalConfig,
                    transport=None, max_calls: int = 8, blocklist_file: Path | None = None,
                    clock=time.monotonic, stop_requested=lambda: False, stop_file: Path | None = None,
                    server_start_args_sha256: str | None = None) -> dict:
    """Evaluate sequential packets through a transport; fail closed on first error."""
    config.validate()
    if server_start_args_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", server_start_args_sha256):
        raise ValueError("server start arguments SHA-256 must be lowercase hexadecimal")
    run_started = clock()
    if transport is None:
        raise ValueError("local transport is not wired; injected transport is required")
    if type(max_calls) is not int or not 1 <= max_calls <= 12:
        raise ValueError("max-calls must be between 1 and 12")
    sent = load_sent_items(folder)
    manifest = read_json(folder / "manifest.json")
    # Keep the subscription path's scope and privacy gates intact.
    from .judge import validate_scope
    scope = load_scope(scope_file)
    validate_scope(manifest, sent, scope)
    validate_contract(manifest)
    blocklist = load_blocklist(blocklist_file, required=needs_blocklist(scope) or bool(manifest.get("blocklist")))
    validate_packet_privacy(folder, manifest, blocklist)
    if not sent or not 1 <= len(manifest["packets"]) <= max_calls:
        raise ValueError("empty packet folder or max-calls exceeded")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    destination = output_root / "local-judge-runs" / run_id
    destination.mkdir(parents=True, exist_ok=False)
    results = []
    execution = ("local-http" if type(transport).__module__ == "smtw.claims.local_http"
                 else "injected-local")
    all_ids = [item_id for entry in manifest["packets"] for item_id in entry["item_ids"]]

    def summary(error=None, remaining=None, error_record=None):
        pending = list(remaining or [])
        result = {"run": str(destination), "calls": len(results), "execution": execution,
                  "model": config.model, "not_evaluated": len(pending), "error": error,
                  "code_sha256": code_sha256(), "all_item_ids": all_ids,
                  "completed_item_ids": [item_id for item_id in all_ids if item_id not in pending],
                  "remaining_item_ids": pending, "envelope": {"judgments": "array"}}
        if error_record and error_record.get("server_computation_ended") == "UNKNOWN":
            result.update(server_computation_ended="UNKNOWN", next_request="HOLD",
                          operator_notice="서버 연산 종료 미확인 — 다음 요청 보류")
        (destination / "run-summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return result

    for index, entry in enumerate(manifest["packets"], 1):
        remaining = all_ids[sum(len(packet["item_ids"]) for packet in manifest["packets"][:index - 1]):]
        stopped = stop_requested() or (stop_file is not None and stop_file.exists())
        if clock() - run_started > 900 or stopped:
            error = "run_deadline_exceeded" if clock() - run_started > 900 else "stop_requested"
            record = {"timestamp": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
                      "request_id": f"{run_id}-{index:03d}", "claim_ids": remaining, "stage": "run", "kind": error,
                      "http_status": None, "elapsed_ms": int((clock() - run_started) * 1000),
                      "not_evaluated": True, "server_computation_ended": None,
                      "outcome": "local_error", "evaluation": "not_evaluated",
                      "code_sha256": code_sha256(), "envelope": {"judgments": "array"},
                      "server_start_args_sha256": server_start_args_sha256}
            _write_record(output_root, record)
            raise LocalJudgeRunError(summary(error, remaining))
        data = (folder / entry["file"]).read_bytes()
        if _sha(data) != entry["sha256"] or len(data.decode("utf-8")) > 30000:
            raise ValueError("packet changed after preflight")
        validate_contract(manifest)
        if load_scope(scope_file).sha256 != scope.sha256:
            raise ValueError("scope changed after preflight")
        if load_blocklist(blocklist_file, required=blocklist is not None) != blocklist:
            raise ValueError("blocklist changed after preflight")
        validate_packet_privacy(folder, manifest, blocklist)
        subset = {key: sent[key] for key in entry["item_ids"]}
        validate_scope(manifest, sent, scope)
        packet = json.loads(data)
        request_id = f"{run_id}-{index:03d}"
        request = _request(config, packet, entry["sha256"], request_id=request_id,
                           claim_ids=list(entry["item_ids"]), run_deadline=run_started + 900,
                           server_start_args_sha256=server_start_args_sha256)
        started = clock()
        raw, error, error_record, partial_receipt, rows, response = "", None, None, None, None, None
        response_stage = "response"
        try:
            response = transport(request)
            raw = response.get("body", "") if isinstance(response, dict) and isinstance(response.get("body"), str) else ""
            rows = _decode_response(response, config.model, config)
            if clock() - started > config.deadline_seconds:
                raise TimeoutError()
            response_stage = "schema"
            validate_judgments(rows)
            response_stage = "ids"
            if {row.get("id") for row in rows if isinstance(row, dict)} != set(entry["item_ids"]) or len(rows) != len(entry["item_ids"]):
                raise ValueError("local response must contain every sent id exactly once")
            response_stage = "verification"
            # A fixed-commit item may be contradicted only when target coverage is real.
            covered = {item["id"]: _target_evidence_complete(item) for item in packet["data_block"]}
            for row in rows:
                if row.get("verdict") in {"supported", "contradicted"} and not covered[row["id"]]:
                    row["verdict"] = "not_enough_evidence"
                    row.pop("evidence_ref", None)
                    row.pop("evidence_quote", None)
                    row["rationale"] = "Target evidence is incomplete."
            verified = verify_judgments(rows, subset)
        except Exception as exc:
            candidate = getattr(exc, "record", None)
            if isinstance(candidate, dict):
                error_record = {key: candidate.get(key) for key in
                                ("request_id", "claim_ids", "stage", "kind", "http_status", "elapsed_ms",
                                 "not_evaluated", "server_computation_ended")}
                error = error_record.get("kind") or "local_transport_error"
                partial_receipt = _safe_transport_receipt(getattr(exc, "receipt", None))
            elif isinstance(exc, TimeoutError):
                error = "local_timeout"
            elif isinstance(exc, ValueError):
                error = {"schema": "schema_violation", "ids": "id_mismatch"}.get(
                    response_stage, "local_invalid_response")
            else:
                # Do not log a transport exception: it may contain a URL, token,
                # or packet text.  The fixed code is enough to distinguish it.
                error = "local_transport_error"
            if partial_receipt is None and isinstance(response, dict):
                partial_receipt = _safe_transport_receipt(response.get("transport_receipt"))
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
                  "packet_sha256": entry["sha256"], "item_ids": entry["item_ids"],
                  "sent_sources": sent_sources(subset), "scope_sha256": scope.sha256,
                  "model": config.model, "local_config": asdict(config), **contract(),
                  "request_schema_sha256": _sha(json.dumps(compatibility_schema(),
                                                            separators=(",", ":")).encode("utf-8")),
                  "envelope": {"judgments": "array"},
                  "code_sha256": code_sha256(),
                  "transmitted_data_sha256": request["transmitted_data_sha256"],
                  "server_start_args_sha256": server_start_args_sha256,
                  "elapsed_seconds": round(clock() - started, 3),
                  "output_sha256": _sha(raw.encode("utf-8")), "execution": execution,
                  "outcome": "local_error" if error else "evaluated",
                  "evaluation": "not_evaluated" if error else "evaluated", "error": error,
                  "verification_counts": None if error else verified["counts"],
                  "response_bytes": len(raw.encode("utf-8")), "raw_response_policy": "hash_only",
                  "token_usage": (response.get("usage") if not error and isinstance(response, dict) else None),
                  "transport_receipt": (_safe_transport_receipt(response.get("transport_receipt")) if not error and isinstance(response, dict)
                                        else partial_receipt),
                  "request_id": request_id, "claim_ids": entry["item_ids"], "stage": response_stage,
                  "kind": error, "http_status": (response.get("status") if isinstance(response, dict) else None),
                  "elapsed_ms": int((clock() - started) * 1000),
                  "not_evaluated": bool(error), "server_computation_ended": None}
        if error_record:
            record.update(error_record)
            if error_record.get("server_computation_ended") == "UNKNOWN":
                record.update(next_request="HOLD", operator_notice="서버 연산 종료 미확인 — 다음 요청 보류")
        _write_record(output_root, record)
        if error:
            raise LocalJudgeRunError(summary(error, remaining, error_record))
        (destination / f"judgments-{index:03d}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        (destination / f"verified-{index:03d}.json").write_text(json.dumps(verified, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(verified)
    return summary()
