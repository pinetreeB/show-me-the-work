"""Validate offline judge fixtures and verify quotes only against sent excerpts."""
from __future__ import annotations

from collections import Counter
import hashlib
from html import escape
import json
from pathlib import Path
import re

from .markup import quote_in_text
from .packets import encoded, redact

TITLE = "LLM 참고 표시 — 판정 아님"


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("중복 JSON 키")
        result[key] = value
    return result


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_object)


def validate_judgments(value) -> list[dict]:
    schema = read_json(Path(__file__).with_name("judge-output.schema.json"))
    if not isinstance(value, list):
        raise ValueError("판정자 출력은 JSON 배열이어야 합니다.")
    rule = schema["items"]
    ids = set()
    for item in value:
        if (not isinstance(item, dict) or not set(rule["required"]) <= item.keys()
                or not item.keys() <= rule["properties"].keys()):
            raise ValueError("판정자 출력 스키마 위반: 항목 필드")
        for key, field in item.items():
            spec = rule["properties"][key]
            types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
            valid_type = (isinstance(field, str) and "string" in types
                          or type(field) is int and "integer" in types
                          or field is None and "null" in types)
            if not valid_type:
                raise ValueError(f"판정자 출력 스키마 위반: {key} 형식")
            if isinstance(field, str) and not spec.get("minLength", 0) <= len(field) <= spec.get("maxLength", len(field)):
                raise ValueError(f"판정자 출력 스키마 위반: {key} 길이")
            if "enum" in spec and field not in spec["enum"]:
                raise ValueError(f"판정자 출력 스키마 위반: {key} 값")
            if type(field) is int and field < spec.get("minimum", field):
                raise ValueError(f"판정자 출력 스키마 위반: {key} 범위")
        if item["id"] in ids:
            raise ValueError("중복 판정 id")
        ids.add(item["id"])
    return value


def load_sent_items(folder: Path) -> dict[str, dict]:
    manifest = read_json(folder / "manifest.json")
    if manifest.get("version") != 1 or manifest.get("model_calls") != 0:
        raise ValueError("지원하지 않는 패킷 manifest")
    items, files = {}, set()
    for entry in manifest["packets"]:
        name = entry["file"]
        if not isinstance(name, str) or not re.fullmatch(r"packet-\d+\.json", name) or name in files:
            raise ValueError("잘못되거나 중복된 패킷 파일명")
        files.add(name)
        path = folder / name
        if path.resolve().parent != folder.resolve():
            raise ValueError("패킷 폴더 밖 파일")
        data = path.read_bytes()
        text = data.decode("utf-8")
        if (hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["bytes"]
                or len(text) != entry["characters"] or len(text) > 30000):
            raise ValueError("패킷 SHA/크기 불일치")
        packet = json.loads(text, object_pairs_hook=_object)
        if packet.get("version") != 1 or not isinstance(packet.get("data_block"), list):
            raise ValueError("잘못된 패킷 데이터 블록")
        if [item["id"] for item in packet["data_block"]] != entry["item_ids"]:
            raise ValueError("manifest 항목 id 불일치")
        for item in packet["data_block"]:
            if item["id"] in items or len(encoded(item)) > 6000:
                raise ValueError("중복 id 또는 항목 크기 초과")
            excerpts = item["excerpts"]
            if not 1 <= len(excerpts) <= 3 or [e["ref"] for e in excerpts] != list(range(1, len(excerpts) + 1)):
                raise ValueError("발췌 번호/개수 오류")
            if any(not isinstance(e["text"], str) or len(e["text"]) > 1500 for e in excerpts):
                raise ValueError("발췌 형식/크기 오류")
            items[item["id"]] = item
    return items


def verify_judgments(judgments, sent: dict[str, dict]) -> dict:
    judgments = validate_judgments(judgments)
    if any(row["id"] not in sent for row in judgments):
        raise ValueError("보낸 패킷에 없는 판정 id")
    counts, displayed = Counter(), []
    for row in judgments:
        verdict = row["verdict"]
        if verdict == "not_enough_evidence":
            counts["not_enough_evidence"] += 1
            continue
        quote, ref = row.get("evidence_quote", ""), row.get("evidence_ref")
        if not quote.strip() or ref is None:
            counts["missing_quote_or_ref"] += 1
            counts["not_enough_evidence"] += 1
            continue
        excerpt = next((e for e in sent[row["id"]]["excerpts"] if e["ref"] == ref), None)
        # Source line numbers are presentation metadata, not quote evidence.
        content = re.sub(r"(?m)^\d+: ?", "", excerpt["text"]) if excerpt else ""
        if (len(quote.strip()) < 8 or "[REDACTED:" in quote.upper()
                or not content or not quote_in_text(quote, content, prose=True)):
            counts["quote_not_in_excerpt"] += 1
            counts["not_enough_evidence"] += 1
            continue
        # Normalize a literal substring only when its containing line is unique.
        # Keep the judge's raw quote for independent reproducibility reporting.
        matches = list(dict.fromkeys(line for line in content.splitlines() if quote in line))
        exact = quote in content.splitlines()
        if len(matches) > 1 and not exact:
            counts["quote_not_in_excerpt"] += 1
            counts["not_enough_evidence"] += 1
            continue
        normalized_quote = quote if exact else matches[0] if matches else quote
        if len(normalized_quote) > 200 or "[REDACTED:" in normalized_quote.upper():
            counts["quote_not_in_excerpt"] += 1
            counts["not_enough_evidence"] += 1
            continue
        counts["quote_verified"] += 1
        counts[verdict] += 1
        displayed.append({**row, "evidence_quote": normalized_quote, "raw_evidence_quote": quote,
                          "rationale": redact(row["rationale"])[0], "quote_verified": True})
    displayed.sort(key=lambda row: (row["verdict"] != "contradicted", row["id"]))
    return {"section_title": TITLE, "items": displayed,
            "counts": {key: counts[key] for key in ("supported", "contradicted", "not_enough_evidence",
                        "quote_verified", "quote_not_in_excerpt", "missing_quote_or_ref")},
            "unjudged_count": len(sent) - len(judgments), "model_calls": 0}


def verify_files(judgments: Path, packets: Path) -> dict:
    return verify_judgments(read_json(judgments), load_sent_items(packets))


def render_advisory(result: dict) -> str:
    def safe(text):
        return escape(re.sub(r"[\x00-\x1f\x7f]", " ", text))
    lines = [TITLE, "", "참고 표시이며 원문 대조기의 상태·요약·종료 코드를 바꾸지 않습니다.", ""]
    for row in result["items"]:
        text = f"[{row['verdict']} · quote_verified] {safe(row['id'])}: {safe(row['rationale'])}"
        if row["verdict"] == "contradicted":
            lines += ["- " + text, "  > " + safe(row["evidence_quote"])]
        else:
            lines.append('<span style="color:gray">' + text + "</span>")
    lines += ["", f"not_enough_evidence: {result['counts']['not_enough_evidence']}건",
              f"quote_not_in_excerpt: {result['counts']['quote_not_in_excerpt']}건 (표시 제외)",
              f"미판정: {result['unjudged_count']}건"]
    return "\n".join(lines)


def render_with_advisory(checked: dict, result: dict) -> str:
    from .cli import render_text
    return render_text(checked) + "\n\n" + render_advisory(result)
