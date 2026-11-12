import json

import pytest

from smtw.claims.advisory import verify_judgments
from smtw.claims import local_smoke
from smtw.claims.local_judge import verify_local_judgments
from tests.test_claims_local_d2_smoke import (RAW_CANARY, SmokeOpener,
                                              assert_no_raw_body, argv, summaries)


def judgment(**overrides):
    return {"id": "synthetic-001", "verdict": "supported", "rationale": "Literal excerpt.",
            "evidence_ref": 1, "evidence_quote": "MAX_LIMIT = 7", **overrides}


def sent(branch="binding_ambiguous"):
    return {"synthetic-001": {"branch": branch, "excerpts": [{"ref": 1,
            "text": "1: MAX_LIMIT = 7"}]}}


@pytest.mark.parametrize("row", [judgment(),
                                  {key: value for key, value in judgment().items() if key != "evidence_quote"},
                                  {key: value for key, value in judgment().items() if key != "evidence_ref"},
                                  {**judgment(), "evidence_quote": "not in the excerpt"}])
def test_binding_ambiguous_supported_is_capped_before_quote_verification(row):
    normalized, verified = verify_local_judgments([row], sent())
    assert normalized == [{"id": "synthetic-001", "verdict": "not_enough_evidence",
                           "rationale": "Deterministic local evidence safety cap."}]
    assert verified["counts"]["binding_ambiguous_capped"] == 1
    assert verified["normalization"] == [{"id": "synthetic-001", "verdict": "not_enough_evidence",
                                           "reason_code": "binding_ambiguous_capped"}]


@pytest.mark.parametrize("verdict", ["contradicted", "not_enough_evidence"])
def test_binding_ambiguous_only_caps_initial_supported(verdict):
    rows = [judgment(verdict=verdict)]
    normalized, verified = verify_local_judgments(rows, sent())
    assert normalized == rows
    assert verified["counts"]["binding_ambiguous_capped"] == 0
    assert verified["counts"][verdict] == 1
    assert verified["normalization"] == []


def test_other_branch_and_common_verifier_remain_unchanged():
    row = judgment()
    normalized, local = verify_local_judgments([row], sent("paraphrased_quote"))
    common = verify_judgments([row], sent("binding_ambiguous"))
    assert normalized == [row]
    assert local["counts"]["supported"] == common["counts"]["supported"] == 1
    assert local["counts"]["binding_ambiguous_capped"] == 0
    assert "binding_ambiguous_capped" not in common["counts"]


class SupportedBindingOpener(SmokeOpener):
    def __init__(self):
        super().__init__()
        self.answers["d1-12"] = {"id": "d1-12", "verdict": "supported",
                                  "rationale": "The first excerpt uses fast mode.",
                                  "evidence_ref": 1, "evidence_quote": 'MODE = "fast"'}


def test_smoke_surfaces_binding_cap_and_never_persists_raw_body(tmp_path):
    opener = SupportedBindingOpener()
    assert local_smoke.main(argv(tmp_path), opener=opener) == 0
    summary = json.loads(summaries(tmp_path)[0].read_text(encoding="utf-8"))
    comparison = next(row for row in summary["comparison"] if row["id"] == "d1-12")
    assert summary["binding_ambiguous_capped"] == 1
    assert comparison == {"id": "d1-12", "expected": "not_enough_evidence",
                          "normalized_verdict": "not_enough_evidence",
                          "verified_verdict": "not_enough_evidence",
                          "reason_code": "binding_ambiguous_capped", "matches_expected": True}
    receipts = [json.loads(line) for line in (tmp_path / "out" / "calls.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert next(row for row in receipts if row["item_ids"] == ["d1-12"])[
        "verification_counts"]["binding_ambiguous_capped"] == 1
    assert RAW_CANARY not in summaries(tmp_path)[0].read_text(encoding="utf-8")
    assert_no_raw_body(tmp_path)
