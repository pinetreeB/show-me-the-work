from __future__ import annotations

import json
from pathlib import Path

from core.contract import evaluate_pretool_contract


def _decision_for_evidence(tmp_path: Path, evidence: str) -> str:
    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir()
    _ = (state_dir / "contract.json").write_text(
        json.dumps(
            {
                "restated_goal": "DB migrate",
                "acceptance": ["tables updated"],
                "evidence": [evidence],
            }
        ),
        encoding="utf-8",
    )
    decision = evaluate_pretool_contract(
        {
            "project_root": str(tmp_path),
            "tool_name": "Edit",
            "file_paths": ["migrations/001_init.sql"],
            "prompt": "DB migrate",
        }
    )["decision"]
    assert isinstance(decision, str)
    return decision


def test_high_risk_contract_rejects_assumed_evidence(tmp_path: Path) -> None:
    assert _decision_for_evidence(tmp_path, "assumed") == "block"


def test_high_risk_contract_rejects_would_pass_evidence(tmp_path: Path) -> None:
    assert _decision_for_evidence(tmp_path, "would pass") == "block"


def test_high_risk_contract_rejects_should_pass_evidence(tmp_path: Path) -> None:
    assert _decision_for_evidence(tmp_path, "should pass") == "block"


def test_high_risk_contract_rejects_unrun_korean_evidence(tmp_path: Path) -> None:
    assert _decision_for_evidence(tmp_path, "미실행") == "block"
