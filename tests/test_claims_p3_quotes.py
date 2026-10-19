import hashlib
import json
from pathlib import Path

from smtw.claims.advisory import verify_judgments
from smtw.claims.judge_contract import PROMPT, contract, validate_contract
from smtw.claims.judge_eval import score, score_table
from smtw.claims.packets import write_packets
from claims_judge_support import make_scope


def judgment(quote):
    return {"id": "example", "verdict": "supported", "evidence_ref": 1,
            "evidence_quote": quote, "rationale": "Literal source evidence."}


def item(text="7:     return calculate_value(input_value)"):
    return {"id": "example", "claim": "The function returns a calculated value.",
            "before": "", "after": "", "redactions": {},
            "excerpts": [{"ref": 1, "path": "~/example-repo/module.py",
                          "text": text, "source": "transcript_read"}]}


def test_substrings_normalize_to_entire_line_and_preserve_raw():
    sent = {"example": item()}
    for quote in ["calculate_value", "return calculate_value(input_value)", "input_value"]:
        verified = verify_judgments([judgment(quote)], sent)
        result = verified["items"][0]
        assert result["evidence_quote"] == "    return calculate_value(input_value)"
        assert result["raw_evidence_quote"] == quote


def test_different_containing_lines_are_ambiguous():
    sent = {"example": item("7: return calculate_value(alpha)\n8: return calculate_value(beta)")}
    result = verify_judgments([judgment("calculate_value")], sent)
    assert not result["items"] and result["counts"]["quote_not_in_excerpt"] == 1


def test_reproducibility_has_raw_and_normalized_measures(tmp_path):
    folder = tmp_path / "packets"
    write_packets(folder, [{"version": 1, "data_block": [item()]}], {"case_id": "example-case"})
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps({"items": {"example": {"verdict": "supported"}}}), encoding="utf-8")
    runs = []
    for index, quote in enumerate(["calculate_value", "return calculate_value(input_value)", "input_value"]):
        run = tmp_path / str(index)
        run.mkdir()
        rows = [judgment(quote)]
        (run / "judgments-001.json").write_text(json.dumps(rows), encoding="utf-8")
        (run / "verified-001.json").write_text(json.dumps(verify_judgments(rows, {"example": item()})), encoding="utf-8")
        runs.append(run)
    result = score(folder, truth, runs, scope_file=make_scope(tmp_path))
    assert result["verified_quote_agreement_3"] == 1
    assert result["raw_verified_quote_agreement_3"] == 0
    assert result["verdict_agreement_3"] == 1 and result["model_calls"] == 0
    assert "normalized (3): 1.0000" in score_table(result)
    assert "raw (3): 0.0000" in score_table(result)


def test_prompt_version_and_full_line_contract():
    import pytest
    prompt = PROMPT.read_text(encoding="utf-8")
    assert contract()["version"] == "v1.2"
    assert contract()["prompt_sha256"] == hashlib.sha256(PROMPT.read_bytes()).hexdigest()
    assert "ONE ENTIRE source line" in prompt and "FIRST compare" in prompt
    old = {**contract(), "version": "v1.1"}
    with pytest.raises(ValueError, match="regenerate packets"):
        validate_contract({"judge_contract": old})
