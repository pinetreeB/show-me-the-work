"""Frozen 75512be public decisions; private case inputs are never committed."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from smtw.claims.code_identity import code_sha256

FIXTURE = Path(__file__).parent/"fixtures"/"claims-check-75512be.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def projection(result):
    return {
        "status": result["status"], "summary": result["summary"],
        "verdict_reason_counts": result["verdict_reason_counts"],
        "claims": [
            {**{key: claim.get(key) for key in ("report_line", "check_type", "verdict", "reason_code",
                                               "author_marked", "exempt")},
             **{key+"_sha256": digest(claim.get(key)) for key in ("text", "reason", "source")}}
            for claim in result["claims"]
        ],
    }


def differences(expected, actual):
    changed = [key for key in ("status", "summary", "verdict_reason_counts")
               if expected[key] != actual[key]]
    if len(expected["claims"]) != len(actual["claims"]):
        changed.append("claim_count")
    for index, (before, after) in enumerate(zip(expected["claims"], actual["claims"]), 1):
        changed.extend(f"atom {index} {key}: {before.get(key)} -> {after.get(key)}"
                       for key in before.keys() | after.keys() if before.get(key) != after.get(key))
    return changed


@pytest.mark.parametrize("sample", [f"sample-{index:02}" for index in range(1,13)])
def test_claims_public_75512be_frozen(sample):
    folder = os.environ.get("SMTW_CLAIMS_PUBLIC_RESULTS")
    if not folder:
        if os.environ.get("SMTW_REQUIRE_PUBLIC") == "1":
            pytest.fail("SMTW_REQUIRE_PUBLIC=1 requires fresh public-corpus results")
        pytest.skip("private public-corpus results supplied by authorized regression runner")
    frozen = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert frozen["baseline"] == "75512be" and len(frozen["samples"]) == 12
    actual = json.loads((Path(folder)/(sample+".json")).read_text(encoding="utf-8"))
    assert actual.get("code_sha256") == code_sha256(), "public result code SHA mismatch; regenerate results"
    changed = differences(frozen["samples"][sample], projection(actual))
    assert not changed, sample+"\n"+"\n".join(changed)


@pytest.mark.parametrize("mutation", ["status", "summary", "verdict", "reason_code", "claim_count"])
def test_claims_frozen_comparison_rejects_each_decision_drift(mutation):
    before = {"status":"FAIL", "summary":{"mismatch":1}, "verdict_reason_counts":{},
              "claims":[{"verdict":"mismatch","reason_code":"quote_mismatch"}]}
    after = json.loads(json.dumps(before))
    if mutation in {"status","summary"}:
        after[mutation] = "INCOMPLETE" if mutation=="status" else {"mismatch":0}
    elif mutation=="claim_count":
        after["claims"]=[]
    else:
        after["claims"][0][mutation]="changed"
    assert differences(before,after)
