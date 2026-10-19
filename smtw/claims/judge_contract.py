"""Frozen local prompt and the existing Phase 1 schema."""
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROMPT = HERE / "judge-prompt-v1.txt"
SCHEMA = HERE / "judge-output.schema.json"


def contract():
    return {"version": "v1",
            "prompt_sha256": hashlib.sha256(PROMPT.read_bytes()).hexdigest(),
            "schema_sha256": hashlib.sha256(SCHEMA.read_bytes()).hexdigest()}


def validate_contract(manifest):
    if manifest.get("judge_contract") != contract():
        raise ValueError("prompt/schema contract mismatch; regenerate packets")
