from smtw.claims.assist import select_candidates


def claim(line, text, reason):
    return {"report_line": line, "text": text, "check_type": "K1",
            "verdict": "range_only" if reason == "range_only" else "unverifiable",
            "reason_code": reason}


def test_large_numeric_family_does_not_starve_other_branches():
    lines = [f"Latency is {i} ms." for i in range(100)]
    reasons = ["pointer_missing", "partial_range", "range_only", "paraphrased_quote", "observation_conflict"]
    lines += [f"Module {index} description." for index in range(len(reasons))]
    claims = [claim(101 + index, lines[100 + index], reason) for index, reason in enumerate(reasons)]
    report = "\n".join(lines)
    result = select_candidates(report, {"claims": claims}, fixed_commit=True)
    assert result["selected_count"] == 40
    assert result["items"][0]["branch"] == "observation_conflict"
    assert all(result["selected_branch_counts"][reason] >= 1 for reason in reasons)
    assert sum(result["selected_branch_counts"].values()) == 40
    assert result == select_candidates(report, {"claims": claims}, fixed_commit=True)


def test_same_report_row_multiple_atoms_consume_one_slot():
    text = "alpha.py:3 and beta.py:7 have declarations."
    claims = [claim(1, text, "partial_range"), claim(1, text, "range_only"),
              claim(1, text, "observation_conflict")]
    result = select_candidates(text, {"claims": claims}, fixed_commit=True)
    assert result["candidate_count"] == result["selected_count"] == 1
    assert result["items"][0]["branch"] == "observation_conflict"
    assert set(result["items"][0]["branches"]) == {"partial_range", "range_only", "observation_conflict"}
    assert set(result["items"][0]["paths"]) >= {"alpha.py", "beta.py"}


def test_conflicts_keep_strict_precedence_even_when_limit_is_small():
    report = "\n".join(f"Description {i}." for i in range(6))
    rows = report.splitlines()
    claims = [claim(i + 1, row, "observation_conflict" if i > 1 else "partial_range")
              for i, row in enumerate(rows)]
    result = select_candidates(report, {"claims": claims}, fixed_commit=True, limit=3)
    assert [item["branch"] for item in result["items"]] == ["observation_conflict"] * 3
