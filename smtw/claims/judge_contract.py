"""Frozen prompt, Phase 1 validation schema and CLI delivery schema."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROMPT = HERE / "judge-prompt-v1.txt"
SCHEMA = HERE / "judge-output.schema.json"


def cli_schema(schema: str) -> str:
    """Adapt the fixed array contract to the CLI's object-only schema input."""
    def without_metadata(value):
        if isinstance(value, dict):
            return {key: without_metadata(child) for key, child in value.items()
                    if key not in {"$schema", "$id", "$comment"}}
        if isinstance(value, list):
            return [without_metadata(child) for child in value]
        return value
    array = json.loads(schema)
    if not isinstance(array, dict) or array.get("type") != "array":
        raise ValueError("Phase 1 judgments schema must be an array")
    wrapped = {"type": "object", "properties": {"judgments": without_metadata(array)},
               "required": ["judgments"], "additionalProperties": False}
    # This exact one-line string is both hashed and transported through argv.
    return json.dumps(wrapped, ensure_ascii=True, separators=(",", ":"))


def contract():
    schema = SCHEMA.read_bytes()
    return {"version": "v1.2", "source_policy": "self-identified-read-v1", "privacy_policy": "blocklist-v1",
            "prompt_sha256": hashlib.sha256(PROMPT.read_bytes()).hexdigest(),
            "schema_sha256": hashlib.sha256(schema).hexdigest(),
            "cli_schema_sha256": hashlib.sha256(cli_schema(schema.decode("utf-8")).encode("utf-8")).hexdigest()}


def validate_contract(manifest):
    if manifest.get("judge_contract") != contract():
        raise ValueError("prompt/schema contract mismatch; regenerate packets")
