"""Fail-closed local-judge adapter.

This module deliberately has no HTTP or socket transport.  D1 exercises the
boundary with an injected transport only; the environment owner must supply a
separately reviewed production transport and protocol before a real call can
be made.
"""
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
        address_match = re.fullmatch(r"http://(?:127\.0\.0\.1|\[::1\]):([1-9]\d{0,4})", self.address)
        if (not address_match or int(address_match.group(1)) > 65535 or "@" in self.address):
            raise ValueError("local address must be an explicit loopback HTTP address without credentials")
        if self.json_mode not in {"schema", "json_object"}:
            raise ValueError("unsupported local JSON mode")
        if self.protocol not in {"openai-compatible", "llama-cpp-native", "synthetic-v1"}:
            raise ValueError("unsupported local protocol")
        if self.reasoning not in {"off", "separate"}:
            raise ValueError("local reasoning must be off or separate")


def compatibility_schema() -> dict:
    """Schema sent to a local server, without changing the frozen contract.

    ``evidence_ref`` is optional, so the transport schema can avoid the
    canonical nullable union which some local JSON-schema implementations do
    not accept.  The received response is always checked against SCHEMA.
    """
    canonical = json.loads(SCHEMA.read_text(encoding="utf-8"))
    item = canonical["items"]
    item = {**item, "properties": {**item["properties"]}}
    item["properties"]["evidence_ref"] = {"type": "integer", "minimum": 1}
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

    def strip_atom(value):
        if isinstance(value, dict):
            value.pop("verdict", None)
            value.pop("reason_code", None)
            for child in value.values():
                strip_atom(child)
        elif isinstance(value, list):
            for child in value:
                strip_atom(child)

    for item in visible.get("data_block", []):
        if not isinstance(item, dict):
            continue
        item.pop("branch", None)
        item.pop("branches", None)
        item.pop("verdict", None)
        item.pop("reason_code", None)
        for field in ("atom", "check"):
            if field in item:
                strip_atom(item[field])
    return visible


def _request(config: LocalConfig, packet: dict, packet_sha256: str) -> dict:
    visible_packet = _model_packet(packet)
    return {"protocol": config.protocol, "address": config.address, "model": config.model,
            "deadline_seconds": config.deadline_seconds, "input_tokens": config.input_tokens,
            "output_tokens": config.output_tokens, "json_mode": config.json_mode,
            "seed": config.seed, "temperature": config.temperature, "reasoning": config.reasoning,
            "response_schema": compatibility_schema(), "packet_sha256": packet_sha256,
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
    if set(response) != {"model", "status", "body", "usage"} or not isinstance(response["body"], str):
        raise ValueError("local response envelope is invalid")
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


def run_local_judge(folder: Path, *, scope_file: Path, output_root: Path, config: LocalConfig,
                    transport=None, max_calls: int = 8, blocklist_file: Path | None = None) -> dict:
    """Evaluate packets through an injected fake transport, or refuse safely."""
    config.validate()
    if transport is None:
        raise ValueError("local transport is not wired; injected transport is required")
    if type(max_calls) is not int or not 1 <= max_calls <= 8:
        raise ValueError("max-calls must be between 1 and 8")
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
    for index, entry in enumerate(manifest["packets"], 1):
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
        request = _request(config, packet, entry["sha256"])
        started = time.monotonic()
        raw, error, rows = "", None, None
        try:
            response = transport(request)
            raw = response.get("body", "") if isinstance(response, dict) and isinstance(response.get("body"), str) else ""
            rows = _decode_response(response, config.model, config)
            if time.monotonic() - started > config.deadline_seconds:
                raise TimeoutError()
            validate_judgments(rows)
            if {row.get("id") for row in rows if isinstance(row, dict)} != set(entry["item_ids"]) or len(rows) != len(entry["item_ids"]):
                raise ValueError("local response must contain every sent id exactly once")
            # A fixed-commit item may be contradicted only when target coverage is real.
            covered = {item["id"]: _target_evidence_complete(item) for item in packet["data_block"]}
            for row in rows:
                if row.get("verdict") in {"supported", "contradicted"} and not covered[row["id"]]:
                    row["verdict"] = "not_enough_evidence"
                    row.pop("evidence_ref", None)
                    row.pop("evidence_quote", None)
                    row["rationale"] = "Target evidence is incomplete."
            verified = verify_judgments(rows, subset)
        except TimeoutError:
            error = "local_timeout"
        except ValueError:
            error = "local_invalid_response"
        except Exception:
            # Do not log a transport exception: it may contain a URL, token,
            # or packet text.  The fixed code is enough to distinguish it.
            error = "local_transport_error"
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
                  "packet_sha256": entry["sha256"], "item_ids": entry["item_ids"],
                  "sent_sources": sent_sources(subset), "scope_sha256": scope.sha256,
                  "model": config.model, "local_config": asdict(config), **contract(),
                  "request_schema_sha256": _sha(json.dumps(compatibility_schema(), sort_keys=True,
                                                            separators=(",", ":")).encode("utf-8")),
                  "transmitted_data_sha256": request["transmitted_data_sha256"],
                  "elapsed_seconds": round(time.monotonic() - started, 3),
                  "output_sha256": _sha(raw.encode("utf-8")), "execution": "injected-local",
                  "outcome": "local_error" if error else "evaluated",
                  "evaluation": "not_evaluated" if error else "evaluated", "error": error,
                  "verification_counts": None if error else verified["counts"],
                  "response_bytes": len(raw.encode("utf-8")), "raw_response_policy": "hash_only",
                  "token_usage": (response.get("usage") if not error and isinstance(response, dict) else None)}
        _write_record(output_root, record)
        if error:
            raise ValueError("local judge execution failed; see call receipt")
        (destination / f"judgments-{index:03d}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        (destination / f"verified-{index:03d}.json").write_text(json.dumps(verified, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(verified)
    return {"run": str(destination), "calls": len(results), "execution": "injected-local",
            "model": config.model, "not_evaluated": 0}
