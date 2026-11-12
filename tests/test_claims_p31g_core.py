"""Approved source locks: 75512be plus p4-1 F6/F7/F2 amendments; no case input."""
import ast
import hashlib
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
FROZEN=json.loads((ROOT/"tests/fixtures/claims-core-75512be.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name",sorted(FROZEN))
def test_claims_checker_source_frozen_to_approved_baseline(name):
    source=(ROOT/"smtw/claims"/name).read_text(encoding="utf-8")
    if name=="transcripts.py":
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=="parse_result")
        source=ast.get_source_segment(source,node)
    assert hashlib.sha256(source.encode("utf-8")).hexdigest()==FROZEN[name], name
